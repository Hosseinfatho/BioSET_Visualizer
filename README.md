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

To run the server remotely, and prevent the browser from opening up, do:
```bash
source .venv\bin\activate
# Windows: .venv\Scripts\activate
bioset --server --host 0.0.0.0 --port 1234
```
Then, on the client machine, navigate to ``http://<server-ip-address>:1234/index.html`` in a browser.   
If you are not able to access the server ensure that the server is reacheable from the client network and that there is no firewall blocking access.

## Config

Most relevant settings such as link to the zarr, chanel indices and voxel spacing can be changed in app.py main() function.

Refer to config.py for a full list of possible settings and their meanings.

