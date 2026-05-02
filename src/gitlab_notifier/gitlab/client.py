from urllib.parse import quote

import httpx


class GitLabClient:
    def __init__(self, http: httpx.AsyncClient, token: str):
        self._http = http
        self._headers = {"PRIVATE-TOKEN": token}

    async def get_project(self, path_with_namespace: str) -> dict:
        encoded = quote(path_with_namespace, safe="")
        r = await self._http.get(f"/api/v4/projects/{encoded}", headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def create_webhook(self, project_id: int, *, url: str, secret: str) -> dict:
        body = {
            "url": url,
            "token": secret,
            "push_events": True,
            "tag_push_events": True,
            "merge_requests_events": True,
            "note_events": True,
            "pipeline_events": True,
            "issues_events": True,
            "enable_ssl_verification": True,
        }
        r = await self._http.post(
            f"/api/v4/projects/{project_id}/hooks",
            headers=self._headers,
            json=body,
        )
        r.raise_for_status()
        return r.json()

    async def delete_webhook(self, project_id: int, hook_id: int) -> None:
        r = await self._http.delete(
            f"/api/v4/projects/{project_id}/hooks/{hook_id}",
            headers=self._headers,
        )
        if r.status_code not in (204, 404):
            r.raise_for_status()
