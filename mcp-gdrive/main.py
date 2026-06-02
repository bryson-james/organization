"""
Google Drive MCP server (stdio) for Paperclip agents.

Based on asadudin/mcp-server-gdrive, patched to:
- Load service account credentials from GOOGLE_SERVICE_ACCOUNT_JSON env var
  (raw JSON string OR base64), falling back to GOOGLE_SERVICE_ACCOUNT_FILE path.
- Pass supportsAllDrives=true / includeItemsFromAllDrives=true on every Drive
  API call so Shared Drives work.
- list_files accepts an optional drive_id to scope to one Shared Drive.
- upload_file accepts a parent_id which may be a Shared Drive ID or any
  folder ID inside a Shared Drive.

Designed to run as a stdio subprocess of Claude Code:
    claude mcp add gdrive -- python /app/mcp-gdrive/main.py --transport stdio
"""
import os
import json
import base64
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import httpx
from google.oauth2 import service_account
from google.auth.transport.requests import Request

load_dotenv()

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 8055))
SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SCOPES = [os.environ.get("GOOGLE_DRIVE_SCOPES", "https://www.googleapis.com/auth/drive")]

mcp = FastMCP("gdrive", host=HOST, port=PORT)


def _load_sa_info() -> dict:
    """Load service account JSON from env (raw or base64) or file."""
    if SERVICE_ACCOUNT_JSON:
        raw = SERVICE_ACCOUNT_JSON.strip()
        if not raw.startswith("{"):
            try:
                raw = base64.b64decode(raw).decode("utf-8")
            except Exception as e:
                raise RuntimeError(
                    f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON or base64: {e}"
                )
        return json.loads(raw)
    if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
        with open(SERVICE_ACCOUNT_FILE) as f:
            return json.load(f)
    raise RuntimeError(
        "No service account credentials found. Set GOOGLE_SERVICE_ACCOUNT_JSON "
        "(raw JSON or base64) or GOOGLE_SERVICE_ACCOUNT_FILE."
    )


def get_google_creds():
    info = _load_sa_info()
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


async def make_gdrive_request(
    endpoint,
    method="GET",
    params=None,
    data=None,
    files=None,
    multipart=False,
):
    creds = get_google_creds()
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}", "Accept": "application/json"}
    url = f"https://www.googleapis.com/drive/v3/{endpoint}"
    base_params = {"supportsAllDrives": "true"}
    if params:
        base_params.update(
            {k: str(v) if isinstance(v, bool) else v for k, v in params.items()}
        )
    async with httpx.AsyncClient() as client:
        try:
            if method.upper() == "GET":
                resp = await client.get(
                    url, headers=headers, params=base_params, timeout=30.0
                )
            elif method.upper() == "POST":
                if multipart and files:
                    resp = await client.post(
                        url,
                        headers=headers,
                        params=base_params,
                        files=files,
                        data=data,
                        timeout=60.0,
                    )
                else:
                    resp = await client.post(
                        url,
                        headers=headers,
                        json=data,
                        params=base_params,
                        timeout=30.0,
                    )
            elif method.upper() == "PATCH":
                if multipart and files:
                    resp = await client.patch(
                        url,
                        headers=headers,
                        params=base_params,
                        files=files,
                        data=data,
                        timeout=60.0,
                    )
                else:
                    resp = await client.patch(
                        url,
                        headers=headers,
                        json=data,
                        params=base_params,
                        timeout=30.0,
                    )
            elif method.upper() == "DELETE":
                resp = await client.delete(
                    url, headers=headers, params=base_params, timeout=30.0
                )
            else:
                return {"error": f"Unsupported method: {method}"}
            resp.raise_for_status()
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return {"content": resp.content, "headers": dict(resp.headers)}
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json()
            except Exception:
                detail = {"response_text": e.response.text}
            return {
                "error": f"API Error: {e.response.status_code}",
                "status_code": e.response.status_code,
                "details": detail,
            }
        except httpx.RequestError as e:
            return {"error": f"Request Error: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}


@mcp.tool()
async def list_files(
    page_size: int = 10,
    q: Optional[str] = None,
    page_token: Optional[str] = None,
    drive_id: Optional[str] = None,
) -> str:
    """List files in Google Drive (incl. Shared Drives).

    Args:
        page_size: Max files to return.
        q: Drive query (e.g. "name contains 'report' and mimeType='application/pdf'").
        page_token: Pagination token.
        drive_id: If provided, scope to one Shared Drive.
    """
    params = {
        "pageSize": page_size,
        "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size, webViewLink, parents, driveId)",
        "includeItemsFromAllDrives": "true",
    }
    if drive_id:
        params["corpora"] = "drive"
        params["driveId"] = drive_id
    else:
        params["corpora"] = "allDrives"
    if q:
        params["q"] = q
    if page_token:
        params["pageToken"] = page_token

    response = await make_gdrive_request("files", params=params)
    if "error" in response:
        return json.dumps(
            {"error": response["error"], "details": response.get("details")},
            indent=2,
        )
    return json.dumps(
        {
            "files": response.get("files", []),
            "nextPageToken": response.get("nextPageToken"),
        },
        indent=2,
    )


@mcp.tool()
async def get_file_info(file_id: str) -> str:
    """Get metadata for one file (works for Shared Drive files)."""
    params = {
        "fields": "id,name,mimeType,size,webViewLink,createdTime,modifiedTime,owners,shared,parents,driveId"
    }
    response = await make_gdrive_request(f"files/{file_id}", params=params)
    return json.dumps(response, indent=2)


@mcp.tool()
async def create_folder(name: str, parent_id: Optional[str] = None) -> str:
    """Create a folder. If parent_id is a Shared Drive folder ID, the folder lands there."""
    data = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        data["parents"] = [parent_id]
    response = await make_gdrive_request("files", method="POST", data=data)
    return json.dumps(response, indent=2)


@mcp.tool()
async def upload_file(
    name: str,
    content: str,
    mime_type: str = "text/plain",
    parent_id: Optional[str] = None,
) -> str:
    """Upload a file. To upload into a Shared Drive, pass parent_id = the Shared
    Drive ID (or any folder ID inside it). Content must be base64-encoded.
    """
    try:
        file_content = base64.b64decode(content)
        metadata = {"name": name}
        if parent_id:
            metadata["parents"] = [parent_id]
        params = {
            "uploadType": "multipart",
            "fields": "id,name,mimeType,size,webViewLink,parents,driveId",
        }
        files = {
            "metadata": ("metadata", json.dumps(metadata), "application/json"),
            "file": (name, file_content, mime_type),
        }
        response = await make_gdrive_request(
            "files", method="POST", params=params, files=files, multipart=True
        )
        return json.dumps(response, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Upload failed: {str(e)}"}, indent=2)


@mcp.tool()
async def download_file(file_id: str) -> str:
    """Download a file (<=10MB) as base64."""
    try:
        info = await make_gdrive_request(
            f"files/{file_id}", params={"fields": "name,mimeType,size"}
        )
        if "error" in info:
            return json.dumps(info, indent=2)
        size = int(info.get("size", 0) or 0)
        if size > 10 * 1024 * 1024:
            return json.dumps({"error": f"File too large: {size} bytes"}, indent=2)
        resp = await make_gdrive_request(f"files/{file_id}?alt=media")
        if "error" in resp:
            return json.dumps(resp, indent=2)
        encoded = base64.b64encode(resp.get("content", b"")).decode("utf-8")
        return json.dumps(
            {
                "name": info.get("name"),
                "mimeType": info.get("mimeType"),
                "size": info.get("size"),
                "content": encoded,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": f"Download failed: {str(e)}"}, indent=2)


@mcp.tool()
async def share_file(file_id: str, email: str, role: str = "reader") -> str:
    """Grant a user permission on a file. role: reader|writer|commenter|owner."""
    data = {"type": "user", "role": role, "emailAddress": email}
    params = {
        "sendNotificationEmail": "true",
        "fields": "id,type,role,emailAddress",
    }
    response = await make_gdrive_request(
        f"files/{file_id}/permissions", method="POST", data=data, params=params
    )
    return json.dumps(response, indent=2)


@mcp.tool()
async def debug_api_connection() -> str:
    """Sanity check: prove the service account can hit Drive."""
    try:
        creds = get_google_creds()
        creds.refresh(Request())
        about = await make_gdrive_request(
            "about", params={"fields": "user,storageQuota"}
        )
        return json.dumps(
            {
                "token_valid": creds.valid,
                "scopes": creds.scopes,
                "api_test": "success" if "error" not in about else "failed",
                "user_info": about.get("user", {}),
                "storage_quota": about.get("storageQuota", {}),
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        type=str,
        default="stdio",
        help="stdio (default) or sse",
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)
