# BioSET Visualizer

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -U pip
pip install -e .
```

## Run
```bash
bioset
```

## Config

Most relevant settings such as link to the zarr, chanel indices and voxel spacing can be changed in app.py main() function.

Refer to config.py for a full list of possible settings and their meanings.

