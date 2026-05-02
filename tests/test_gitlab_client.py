import httpx

from gitlab_notifier.gitlab.client import GitLabClient


async def test_get_project():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v4/projects/team%2Fapi"
        assert request.headers["PRIVATE-TOKEN"] == "tok"
        return httpx.Response(200, json={"id": 7, "path_with_namespace": "team/api"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://gitlab.example.com") as http:
        c = GitLabClient(http, "tok")
        proj = await c.get_project("team/api")
        assert proj["id"] == 7


async def test_create_webhook():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read()
        return httpx.Response(201, json={"id": 99})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://gitlab.example.com") as http:
        c = GitLabClient(http, "tok")
        hook = await c.create_webhook(7, url="https://bot/example", secret="s")
        assert hook["id"] == 99
        assert seen["path"] == "/api/v4/projects/7/hooks"


async def test_delete_webhook_handles_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://gitlab.example.com") as http:
        c = GitLabClient(http, "tok")
        await c.delete_webhook(7, 1)
