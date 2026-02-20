"""
SQLite ExecutionContext Implementation.
"""
import os
import sqlite3
import pandas as pd
from typing import List, Dict, Any, Optional, Iterator
from pathlib import Path

from .base_context import ExecutionContext, ContextType, ResourceInfo, FieldInfo, RelationshipInfo


class SQLiteContext(ExecutionContext):
    """
    ExecutionContext implementation for SQLite databases.
    """
    
    DEFAULT_CHUNK_SIZE = 10000
    
    def __init__(
        self,
        db_path: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        exclude_resources: Optional[List[str]] = None
    ):
        self._db_path = os.path.abspath(db_path)
        
        if name is None:
            name = Path(db_path).stem
        
        super().__init__(name=name, description=description)
        
        self._exclude_resources = set(exclude_resources or [])
        self._exclude_resources.update(['sqlite_sequence', 'sqlite_stat1'])
        
        self._resources_cache: Optional[List[str]] = None
    
    def _get_connection(self) -> sqlite3.Connection:
        if not os.path.exists(self._db_path):
            raise FileNotFoundError(f"Database not found: {self._db_path}")
        return sqlite3.connect(self._db_path)
    
    @property
    def context_type(self) -> ContextType:
        return ContextType.SQLITE
    
    @property
    def resources(self) -> List[str]:
        if self._resources_cache is not None:
            return self._resources_cache
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            all_resources = [row[0] for row in cursor.fetchall()]
        
        self._resources_cache = [r for r in all_resources if r not in self._exclude_resources]
        return self._resources_cache
    
    def _load_resource_info(self, resource: str) -> ResourceInfo:
        with self._get_connection() as conn:
            cursor = conn.execute(f"PRAGMA table_info('{resource}')")
            pragma_info = cursor.fetchall()
            
            cursor = conn.execute(f"SELECT COUNT(*) FROM '{resource}'")
            item_count = cursor.fetchone()[0]
            
            pk_fields = {row[1] for row in pragma_info if row[5] > 0}
            
            cursor = conn.execute(f"PRAGMA foreign_key_list('{resource}')")
            fk_info = cursor.fetchall()
            fk_fields = {row[3]: f"{row[2]}.{row[4]}" for row in fk_info}
            
            sample_df = pd.read_sql_query(
                f"SELECT * FROM '{resource}' LIMIT 100", conn
            )
            
            fields = []
            for row in pragma_info:
                field_name = row[1]
                field_type = row[2]
                not_null = bool(row[3])
                
                sample_values = []
                if field_name in sample_df.columns:
                    sample_values = sample_df[field_name].dropna().head(5).tolist()
                
                fields.append(FieldInfo(
                    name=field_name,
                    dtype=field_type,
                    nullable=not not_null,
                    is_primary_key=field_name in pk_fields,
                    is_foreign_key=field_name in fk_fields,
                    foreign_key_reference=fk_fields.get(field_name),
                    sample_values=sample_values
                ))
            
            primary_key = None
            if pk_fields:
                primary_key = list(pk_fields)[0] if len(pk_fields) == 1 else list(pk_fields)
        
        return ResourceInfo(
            name=resource,
            item_count=item_count,
            field_count=len(fields),
            fields=fields,
            primary_key=primary_key,
            location=self._db_path
        )
    
    def read_resource(
        self, 
        resource: str, 
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> pd.DataFrame:
        if resource not in self.resources:
            raise ValueError(f"Resource '{resource}' not found. Available: {self.resources}")
        
        field_str = ", ".join(f'"{c}"' for c in fields) if fields else "*"
        query = f'SELECT {field_str} FROM "{resource}"'
        
        if limit:
            query += f" LIMIT {limit}"
        
        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, **kwargs)
    
    def iter_resource(
        self, 
        resource: str, 
        chunksize: int = 10000,
        **kwargs
    ) -> Iterator[pd.DataFrame]:
        if resource not in self.resources:
            raise ValueError(f"Resource '{resource}' not found. Available: {self.resources}")
        
        query = f'SELECT * FROM "{resource}"'
        
        with self._get_connection() as conn:
            for chunk in pd.read_sql_query(query, conn, chunksize=chunksize, **kwargs):
                yield chunk
    
    def get_db_path(self) -> str:
        return self._db_path
    
    def execute_query(self, query: str, params: tuple = None) -> pd.DataFrame:
        with self._get_connection() as conn:
            if params:
                return pd.read_sql_query(query, conn, params=params)
            return pd.read_sql_query(query, conn)
    
    def _discover_relationships(self) -> List[RelationshipInfo]:
        relationships = []
        
        with self._get_connection() as conn:
            for resource in self.resources:
                cursor = conn.execute(f"PRAGMA foreign_key_list('{resource}')")
                fk_rows = cursor.fetchall()
                
                for fk in fk_rows:
                    to_resource = fk[2]
                    from_field = fk[3]
                    to_field = fk[4]
                    
                    relationships.append(RelationshipInfo(
                        from_resource=resource,
                        from_field=from_field,
                        to_resource=to_resource,
                        to_field=to_field,
                        relationship_type="many-to-one",
                        confidence=1.0,
                        is_verified=True,
                        description=f"Foreign key constraint from {resource}.{from_field}"
                    ))
        
        return relationships
    
    def get_resource_ddl(self, resource: str) -> str:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (resource,)
            )
            result = cursor.fetchone()
            return result[0] if result else ""
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["db_path"] = self._db_path
        base["file_size_bytes"] = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else None
        return base