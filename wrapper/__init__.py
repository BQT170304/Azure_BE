import os
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.cosmos import CosmosClient
from datetime import datetime, timedelta
import qrcode
from dotenv import dotenv_values
import azure.functions as func

config = dotenv_values(".env")

app = FastAPI()

# Initialize Azure Blob Storage and Cosmos DB clients
blob_service_client = BlobServiceClient.from_connection_string(config["AZURE_STORAGE_CONNECTION_STRING"])
cosmos_client = CosmosClient(config["COSMOS_DB_ENDPOINT"], config["COSMOS_DB_KEY"])
database = cosmos_client.get_database_client(config["DATABASE_NAME"])
container = database.get_container_client(config["CONTAINER_NAME"])

@app.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    try:
        responses = []
        for file in files:
            blob_name = file.filename
            blob_client = blob_service_client.get_blob_client(container="uploads", blob=blob_name)
            blob_client.upload_blob(file.file)
         
            sas_token = generate_blob_sas(blob_service_client.account_name,
                                          "uploads", blob_name,
                                          account_key=blob_service_client.credential.account_key,
                                          permission=BlobSasPermissions(read=True),
                                          expiry=datetime.utcnow() + timedelta(hours=24))
            
            download_url = f"https://{blob_service_client.account_name}.blob.core.windows.net/uploads/{blob_name}?{sas_token}"
            
            metadata = {
                "id": blob_name,
                "url": download_url,
                "limit": 100,
                "downloaded": 0,
                "expiry": (datetime.utcnow() + timedelta(hours=24)).isoformat()
            }
            container.create_item(metadata)
            
            responses.append({
                "filename": blob_name,
                "download_url": download_url,
            })
        
        return responses
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# def generate_qr_code(url: str):
#     qr = qrcode.QRCode(
#         version=1,
#         error_correction=qrcode.constants.ERROR_CORRECT_L,
#         box_size=10,
#         border=4
#     )
#     qr.add_data(url)
#     qr.make(fit=True)
#     img = qr.make_image(fill_color="black", back_color="white")
#     qr_code_file = f"/tmp/{url.split('/')[-1]}.png"
#     img.save(qr_code_file)
#     return qr_code_file

@app.get("/download/{file_id}")
async def download_file(file_id: str):
    try:
        item = container.read_item(item=file_id, partition_key=file_id)
        if item["downloaded"] >= item["limit"] or datetime.fromisoformat(item["expiry"]) < datetime.utcnow():
            raise HTTPException(status_code=403, detail="Download limit reached or file expired")

        item["downloaded"] += 1
        container.replace_item(item=file_id, body=item)
        return JSONResponse(content={"url": item["url"], "remaining_downloads": item["limit"] - item["downloaded"]})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def read_root():
    return {"Hello": "World"}

def main(req: func.HttpRequest) -> func.HttpResponse:
    return func.AsgiMiddleware(app).handle(req)