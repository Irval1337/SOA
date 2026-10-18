from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import yaml
from openapi_core import OpenAPI
from openapi_core.datatypes import RequestParameters
from werkzeug.datastructures import Headers, ImmutableMultiDict

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi" / "openapi.yaml"


class _Request:
    def __init__(self, request: httpx.Request) -> None:
        url = urlparse(str(request.url))
        self.host_url = f"{url.scheme}://{url.netloc}"
        self.path = url.path
        self.method = request.method.lower()
        self.body = request.content or None
        self.content_type = request.headers.get("content-type", "application/json")
        self.parameters = RequestParameters(
            query=ImmutableMultiDict(parse_qs(url.query)),
            header=Headers(dict(request.headers)),
            cookie=ImmutableMultiDict(),
        )


class _Response:
    def __init__(self, response: httpx.Response) -> None:
        self.status_code = response.status_code
        self.content_type = response.headers.get("content-type", "")
        self.headers = Headers(dict(response.headers))
        self.data = response.content


class ContractValidator:
    def __init__(self, base_url: str) -> None:
        spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
        spec["servers"] = [{"url": base_url}]
        self.openapi = OpenAPI.from_dict(spec)

    def validate(self, response: httpx.Response) -> None:
        self.openapi.validate_response(_Request(response.request), _Response(response))
