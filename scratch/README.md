# Testing and understanding the project
Creator: Na Li \
Date: 28th April 2026

## Env setup
- python: 3.12.13
- dependencies: `requirements.txt`

```bash
make uv-setup
```

## Manul tests

### LLM API connection
:white_check_mark: `python -m scratch.connection`

### LLM completion
:white_check_mark: `python -m scratch.completion`

### Metadata generation pipeline 
- Sample datasets: `scratch/input/biota/biota.csv`
- Other datasets: `scratch/input/ns`
    - [At-risk bees](https://geohub-natureserve.opendata.arcgis.com/datasets/03ffd74826da460ca1011aefa4290c6a_11/explore?location=-68.351342%2C39.375000%2C0)
    - [At-risk Plants](https://geohub-natureserve.opendata.arcgis.com/datasets/3ea0e3207989438ca036b598527c7562_7/explore?location=7.439587%2C0.000000%2C2.00)


:white_check_mark: metadata generation pipeline test: `python -m scratch.generation`

Output: 
```python
{
  "title": "my_dataset",
  "description": "Dataset containing biotic measurements including abundance and fine dead matter per square meter.",
  "subject": "Biotic measurements",
  "spatial_coverage": null,
  "spatial_resolution": null,
  "temporal_coverage": null,
  "temporal_resolution": null,
  "methods": null,
  "format": "CSV"
}
```

