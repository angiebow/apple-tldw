"""tldw / ViReel cloud API package.

The async, storage-backed rewrite of the local `server.py` for App Store
distribution: clients upload media to object storage, enqueue a job, poll it,
then download results. See openapi.yaml for the contract and README.md for how
to run it locally.
"""
