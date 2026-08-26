<p align='center'>
  <img src="src\bioset\ui\assets\icon.jpg" width=150 />
</p>


<h1 align='center'>
  BioSET
  Visualizer
</h1>

# Introduction
Project is being actively developed.

## Setup

### Clone (including the Biomni submodule)

```bash
git clone --recurse-submodules https://github.com/Chahat08/BioSET_Visualizer.git
```

If you already cloned without `--recurse-submodules`, initialise the submodule afterwards:

```bash
git submodule update --init --recursive
```

To pull the latest changes including any submodule updates:

```bash
git pull
git submodule update --recursive --remote
```

### BioSET Python environment

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -U pip
pip install -e .
```

## Running
The rendering is performed at the server's side. The following starts a local server instance, and opens the client in a browser (at [http://localhost:8080/index.html](http://localhost:8080/index.html)).

```bash
source .venv\bin\activate
# Windows: .venv\Scripts\activate
bioset
```
The default port is ``8080``. To change this, you can supply a port command line argument such as ``bioset --port 1234``.  

By default, console output is suppressed. To enable logging/print statements, use the ``--logs`` flag:
```bash
bioset --logs
```

Each browser visit gets its own isolated rendering process, so with ``--logs`` every
line is prefixed with the short id of the session it came from:

```
[bioset] session 878f5977 on 127.0.0.1:55788
[878f5977] [callbacks] Loaded 12 channels
[a10e2662] [callbacks] Loaded 12 channels
```

A copy of each session's output is also kept in ``bioset-session-<id>.log`` in the
system temp directory. Pass ``--single`` to run one shared process instead, in which
case the state survives a page refresh and the logs are unprefixed.

To run the server remotely, and prevent the browser from opening up, do:
```bash
source .venv\bin\activate
# Windows: .venv\Scripts\activate
bioset --server --host 0.0.0.0 --port 1234
```
Then, on the client machine, navigate to ``http://<server-ip-address>:1234/index.html`` in a browser.   
If you are not able to access the server ensure that the server is reacheable from the client network and that there is no firewall blocking access.

### Datasets

Rather than pasting paths into the Data Sources panel every session, list the datasets
you use in a ``datasets.json`` at the repo root. An optional **Name** field then appears
above the Zarr URL; the arrow on its right lists your presets, and picking one fills in
the paths below. You can also just type a name, or leave it blank. Nothing is loaded
until you press **Load Data** / **Load Analysis**, so a wrong pick costs nothing.

```json
{
  "datasets": [
    {
      "name": "MIS",
      "zarr_url": "D:\\Melanoma\\rechunked_mis.zarr",
      "separate_metadata": true,
      "metadata_url": "https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/OME/METADATA.ome.xml",
      "analysis_dir": "D:\\results\\mis_v3"
    },
    {
      "name": "STIC",
      "zarr_url": "D:\\STIC\\stic_tr4_lsp18251.zarr",
      "separate_metadata": false,
      "analysis_dir": "D:\\results\\stic_full"
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| ``name`` | yes | Label shown in the dropdown |
| ``zarr_url`` | yes | Local path or URL of the zarr store |
| ``separate_metadata`` | no (default ``false``) | ``true`` when channel names come from a separate OME-XML rather than the store itself. Opens the **Separate metadata** panel and switches the name source |
| ``metadata_url`` | only if the above is ``true`` | Path or URL of the OME-XML |
| ``analysis_dir`` | no | Results directory (``colocalization.zarr`` + ``tally/``) |

The file is looked up in this order, first hit wins: ``$BIOSET_DATASETS``,
``./datasets.json`` in the working directory, then the repo root. With no file present
the dropdown is simply hidden and the URL fields behave as before. A malformed file
prints a ``[datasets]`` warning and is skipped — it never blocks startup.

## AI Assistant (Biomni)

BioSET includes an AI assistant powered by [Biomni](https://github.com/snap-stanford/Biomni) for biological labelling and free-form queries about active markers.

### Prerequisites

- [Conda](https://docs.conda.io/en/latest/) (Anaconda or Miniconda)
- Anthropic API key

### Environment setup

Biomni runs in its own conda environment, separate from the main BioSET environment.

```bash
cd Biomni/biomni_env
conda env create -f environment.yml
conda activate biomni_e1
```

### API key

Copy `.env.example` to `.env` and fill in your Anthropic API key:

```bash
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

### Starting the Biomni server

The Biomni server must be running before using the AI assistant in BioSET. Start it in a **separate terminal** with the `biomni_e1` environment active:

```bash
conda activate biomni_e1
cd BioSET_Visualizer   # repo root
python Biomni/run_server.py --port 5000
```

The server listens on `http://localhost:5000` by default. Leave this terminal open while using BioSET.

### Using the AI assistant

1. Start BioSET normally in a separate terminal (`bioset --logs`)
2. Load data and activate channels
3. Open the **AI Assistant** panel in the right drawer
4. Click **Initialize** — this connects to the local Biomni server and loads the agent (takes mutiple minutes on the first run while datasets download)
5. Use **Label** to generate biological labels for the currently active markers (screenshot is sent automatically)
6. Type a free-form question and press **Send** to query the agent about the active markers

## Config

Most relevant settings such as link to the zarr, channel indices and voxel spacing can be changed in `app.py` `main()` function.

Refer to `config.py` for a full list of possible settings and their meanings.

