# Examples

## Environment setup
- python: 3.11+
- dependencies: `pyproject.toml`

```bash
make uv-setup
```

The catalog resolver and the field router are commands, not examples: see
[Tutorials](tutorials/index.md), and [Quickstart](quickstart.md) to get going.

## Scripts

### Describe a bundle's columns
Runs catalog resolution on its own and prints what it produced: every column's resolved
meaning, units, tier and confidence; the citation and corroboration behind one; a search
of the enriched catalog reaching a column whose name says nothing (`ucrit`); and the same
bundle resolved from the README's glossary once the codebook is taken away.

```bash
python examples/describe_columns.py
python examples/describe_columns.py data/tests/router_test
```

### Route with your own judge
Routes a bundle with a column matcher written in code and a passage reader whose model
call is a stub — the two seams every LLM role in the pipeline hangs on.

```bash
python examples/route_with_your_own_judge.py
```

### LLM API connection
```bash
python -m examples.connection
```

### LLM completion
```bash
python -m examples.completion
```

### Metadata generation pipeline 
- Sample datasets: `data/biota/biota.csv`
- Other datasets: `data/ns`
    - [At-risk bees](https://geohub-natureserve.opendata.arcgis.com/datasets/03ffd74826da460ca1011aefa4290c6a_11/explore?location=-68.351342%2C39.375000%2C0)
    - [At-risk Plants](https://geohub-natureserve.opendata.arcgis.com/datasets/3ea0e3207989438ca036b598527c7562_7/explore?location=7.439587%2C0.000000%2C2.00)


```bash
python -m examples.generation
```


Output: 
```python
{
  "title": "my_dataset",
  "description": "Dataset containing biological abundance and aboveground fine dead matter measurements.",
  "subject": "Biological abundance and aboveground fine dead matter",
  "spatial_coverage": null,
  "spatial_resolution": null,
  "temporal_coverage": null,
  "temporal_resolution": null,
  "methods": "Data collected through sampling methods with identification using SIBES IDs.",
  "format": "CSV"
}
```

You can get the full log info by adding the following to `.env`: 
```
LOG_MODE = full
```
