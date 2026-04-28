# Testing and understanding the project
Creator: Na Li \
Date: 28th April 2026

## Env setup
- python: 3.12.13
- dependencies: `requirements.txt`

```bash
make uv-setup
```

## General test
- :white_check_mark: LLM API connection test: `scratch/connection.py`
- :white_check_mark: LLM completion test: `scratch/completion.py`

## Metadata generation test
- Data source: [At-risk bees](https://geohub-natureserve.opendata.arcgis.com/datasets/03ffd74826da460ca1011aefa4290c6a_11/explore?location=-68.351342%2C39.375000%2C0); 
[At-risk Plants](https://geohub-natureserve.opendata.arcgis.com/datasets/3ea0e3207989438ca036b598527c7562_7/explore?location=7.439587%2C0.000000%2C2.00)
- Dataset: `data/sample`

### Tests
- :white_check_mark: metadata generation pipeline test: `scratch/generation.py`
