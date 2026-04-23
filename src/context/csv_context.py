"""
CSV ExecutionContext Implementation.
"""
import os
import pandas as pd
from typing import List, Dict, Any, Optional, Iterator, Union
from pathlib import Path

from .base_context import ExecutionContext, ContextType, ResourceInfo, FieldInfo, RelationshipInfo


class CSVContext(ExecutionContext):
    """
    ExecutionContext implementation for CSV files.
    """
    
    LARGE_FILE_THRESHOLD = 50 * 1024 * 1024
    DEFAULT_CHUNK_SIZE = 10000
    
    def __init__(
        self,
        source: Union[str, List[str], Dict[str, str]],
        name: str = "csv_context",
        description: Optional[str] = None,
        delimiter: Optional[str] = None,
        encoding: str = "utf-8",
        **read_options
    ):
        super().__init__(name=name, description=description)
        
        self._resources: Dict[str, str] = self._normalize_source(source)
        self._delimiter = delimiter
        self._encoding = encoding
        self._read_options = read_options
        self._delimiter_cache: Dict[str, str] = {}
    
    def _normalize_source(
        self, 
        source: Union[str, List[str], Dict[str, str]]
    ) -> Dict[str, str]:
        """Convert various input formats to dict of resource_name -> path."""
        if isinstance(source, str):
            resource_name = Path(source).stem
            return {resource_name: os.path.abspath(source)}
        
        elif isinstance(source, list):
            return {
                Path(p).stem: os.path.abspath(p)
                for p in source
            }
        
        elif isinstance(source, dict):
            return {
                name: os.path.abspath(path)
                for name, path in source.items()
            }
        
        else:
            raise ValueError(f"Invalid source type: {type(source)}")
    
    def _detect_delimiter(self, file_path: str) -> str:
        """Auto-detect the delimiter used in a CSV file."""
        if file_path in self._delimiter_cache:
            return self._delimiter_cache[file_path]
        
        if self._delimiter:
            self._delimiter_cache[file_path] = self._delimiter
            return self._delimiter
        
        try:
            with open(file_path, 'r', encoding=self._encoding) as f:
                sample = f.read(8192)
            
            delimiters = [',', '\t', ';', '|']
            counts = {d: sample.count(d) for d in delimiters}
            best_delimiter = max(counts, key=counts.get)
            
            lines = sample.split('\n')[:5]
            if lines:
                col_counts = [len(line.split(best_delimiter)) for line in lines if line.strip()]
                if col_counts and len(set(col_counts)) == 1 and col_counts[0] > 1:
                    self._delimiter_cache[file_path] = best_delimiter
                    return best_delimiter
            
            self._delimiter_cache[file_path] = ','
            return ','
            
        except Exception:
            self._delimiter_cache[file_path] = ','
            return ','
    
    def _get_read_kwargs(self, resource: str) -> Dict[str, Any]:
        """Get kwargs for pandas read_csv."""
        file_path = self._resources[resource]
        delimiter = self._detect_delimiter(file_path)
        
        kwargs = {
            'delimiter': delimiter,
            'encoding': self._encoding,
            **self._read_options
        }
        return kwargs
    
    def _is_large_file(self, file_path: str) -> bool:
        """Check if file exceeds the large file threshold."""
        try:
            return os.path.getsize(file_path) > self.LARGE_FILE_THRESHOLD
        except OSError:
            return False
    
    @property
    def context_type(self) -> ContextType:
        if len(self._resources) > 1:
            return ContextType.MULTI_CSV
        return ContextType.SINGLE_CSV
    
    @property
    def resources(self) -> List[str]:
        return list(self._resources.keys())
    
    def _load_resource_info(self, resource: str) -> ResourceInfo:
        """Load metadata for a CSV file."""
        file_path = self._resources[resource]
        kwargs = self._get_read_kwargs(resource)
        
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None
        
        sample_df = pd.read_csv(file_path, nrows=100, **kwargs)
        
        if self._is_large_file(file_path):
            item_count = sum(
                len(chunk) 
                for chunk in pd.read_csv(
                    file_path, 
                    chunksize=self.DEFAULT_CHUNK_SIZE,
                    usecols=[0],
                    **kwargs
                )
            )
        else:
            item_count = len(pd.read_csv(file_path, usecols=[0], **kwargs))
        
        fields = []
        for col in sample_df.columns:
            col_data = sample_df[col]
            fields.append(FieldInfo(
                name=col,
                dtype=str(col_data.dtype),
                nullable=col_data.isnull().any(),
                sample_values=col_data.dropna().head(5).tolist()
            ))
        
        return ResourceInfo(
            name=resource,
            item_count=item_count,
            field_count=len(fields),
            fields=fields,
            location=file_path,
            size_in_bytes=file_size
        )
    
    def read_resource(
        self, 
        resource: str, 
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> pd.DataFrame:
        """Read a CSV file into a DataFrame."""
        if resource not in self._resources:
            raise ValueError(f"Resource '{resource}' not found. Available: {self.resources}")
        
        file_path = self._resources[resource]
        read_kwargs = {**self._get_read_kwargs(resource), **kwargs}
        
        if fields:
            read_kwargs['usecols'] = fields
        if limit:
            read_kwargs['nrows'] = limit
        
        return pd.read_csv(file_path, **read_kwargs)
    
    def iter_resource(
        self, 
        resource: str, 
        chunksize: int = 10000,
        **kwargs
    ) -> Iterator[pd.DataFrame]:
        """Iterate over a CSV file in chunks."""
        if resource not in self._resources:
            raise ValueError(f"Resource '{resource}' not found. Available: {self.resources}")
        
        file_path = self._resources[resource]
        read_kwargs = {**self._get_read_kwargs(resource), **kwargs}
        
        return pd.read_csv(file_path, chunksize=chunksize, **read_kwargs)
    
    def get_file_path(self, resource: str) -> str:
        if resource not in self._resources:
            raise ValueError(f"Resource '{resource}' not found. Available: {self.resources}")
        return self._resources[resource]
    
    def get_all_file_paths(self) -> Dict[str, str]:
        return self._resources.copy()
    
    def get_delimiter(self, resource: str) -> str:
        file_path = self._resources[resource]
        return self._detect_delimiter(file_path)
    
    def _discover_relationships(self) -> List[RelationshipInfo]:
        if not self.is_multi_csv:
            return []
        
        relationships = []
        resources_data: Dict[str, pd.DataFrame] = {}
        
        for resource in self.resources:
            try:
                resources_data[resource] = self.read_resource(resource, limit=1000)
            except Exception:
                continue
        
        resource_list = list(resources_data.keys())
        for i, resource_a in enumerate(resource_list):
            for resource_b in resource_list[i+1:]:
                df_a = resources_data[resource_a]
                df_b = resources_data[resource_b]
                
                for field_a in df_a.columns:
                    field_a_norm = field_a.lower().replace("_", "").replace("-", "")
                    
                    for field_b in df_b.columns:
                        field_b_norm = field_b.lower().replace("_", "").replace("-", "")
                        
                        name_match = (
                            field_a_norm == field_b_norm or
                            (field_a_norm.endswith("id") and field_b_norm.endswith("id") and
                             field_a_norm[:-2] == field_b_norm[:-2]) or
                            field_a_norm in field_b_norm or field_b_norm in field_a_norm
                        )
                        
                        if not name_match:
                            continue
                        
                        values_a = set(df_a[field_a].dropna().unique())
                        values_b = set(df_b[field_b].dropna().unique())
                        
                        if not values_a or not values_b:
                            continue
                        
                        intersection = values_a & values_b
                        if not intersection:
                            continue
                        
                        match_rate = len(intersection) / min(len(values_a), len(values_b))
                        
                        if match_rate > 0.1:
                            unique_ratio_a = len(values_a) / len(df_a)
                            unique_ratio_b = len(values_b) / len(df_b)
                            
                            if unique_ratio_a > 0.9 and unique_ratio_b > 0.9:
                                rel_type = "one-to-one"
                            elif unique_ratio_a > 0.9:
                                rel_type = "one-to-many"
                            elif unique_ratio_b > 0.9:
                                rel_type = "many-to-one"
                            else:
                                rel_type = "many-to-many"
                            
                            confidence = match_rate * 0.8 + 0.2
                            
                            relationships.append(RelationshipInfo(
                                from_resource=resource_a,
                                from_field=field_a,
                                to_resource=resource_b,
                                to_field=field_b,
                                relationship_type=rel_type,
                                confidence=round(confidence, 3),
                                is_verified=False
                            ))
        
        relationships.sort(key=lambda r: r.confidence, reverse=True)
        return relationships
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["file_paths"] = self._resources
        base["delimiters"] = {
            resource: self.get_delimiter(resource) 
            for resource in self.resources
        }
        return base