import os
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.cosmos import CosmosClient
from datetime import datetime, timedelta
import qrcode
from dotenv import dotenv_values
import azure.functions as func
import uuid

config = dotenv_values(".env")

app = FastAPI()

# Initialize Azure Blob Storage and Cosmos DB clients
blob_service_client = BlobServiceClient.from_connection_string(config["AZURE_STORAGE_CONNECTION_STRING"])
cosmos_client = CosmosClient(config["COSMOS_DB_ENDPOINT"], config["COSMOS_DB_KEY"])
database = cosmos_client.get_database_client(config["DATABASE_NAME"])
files_container = database.get_container_client(config["FILES_CONTAINER_NAME"])
links_container = database.get_container_client(config["LINKS_CONTAINER_NAME"])

@app.post("/upload")
async def upload_files(files: list[UploadFile] = File(...), limit: int = Form(...)):
    try:
        link_id = str(uuid.uuid4())
        file_metadata_list = []

        for file in files:
            file_id = str(uuid.uuid4())
            blob_name = file.filename
            blob_client = blob_service_client.get_blob_client(container="uploads", blob=blob_name)
            blob_client.upload_blob(file.file)

            sas_token = generate_blob_sas(blob_service_client.account_name,
                                          "uploads", blob_name,
                                          account_key=blob_service_client.credential.account_key,
                                          permission=BlobSasPermissions(read=True),
                                          expiry=datetime.utcnow() + timedelta(hours=24))

            download_url = f"https://{blob_service_client.account_name}.blob.core.windows.net/uploads/{blob_name}?{sas_token}"

            file_metadata = {
                "id": file_id,
                "url": download_url,
                "downloaded": 0,
                "limit": limit,
            }
            files_container.create_item(file_metadata)
            file_metadata.pop("limit")
            file_metadata_list.append(file_metadata)

        link_metadata = {
            "id": link_id,
            "files": file_metadata_list,
            "limit": limit
        }
        links_container.create_item(link_metadata)

        return {"id": link_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/link/{id}")
async def get_link(id: str):
    try:
        link = links_container.read_item(item=id, partition_key=id)
        return {
            "files": link["files"],
            "limit": link["limit"]
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail="Link not found")

@app.get("/download/{link_id}/{file_id}")
async def download_file(link_id: str, file_id: str):
    try:
        file_item = files_container.read_item(item=file_id, partition_key=file_id)
        if file_item["downloaded"] >= file_item["limit"]:
            raise HTTPException(status_code=403, detail="Download limit reached")

        file_item["downloaded"] += 1
        files_container.replace_item(item=file_id, body=file_item)
        
        # Update file download count in links_container
        link_item = links_container.read_item(item=link_id, partition_key=link_id)
        for file in link_item["files"]:
            if file["id"] == file_id:
                file["downloaded"] += 1
                break
        links_container.replace_item(item=link_id, body=link_item)
        
        return {
            "url": file_item["url"]
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=e)

@app.get("/")
async def read_root():
    return {"Hello": "World"}

def main(req: func.HttpRequest) -> func.HttpResponse:
    return func.AsgiMiddleware(app).handle(req)