import hmac
import logging

import grpc

log = logging.getLogger(__name__)

API_KEY_HEADER = "x-api-key"


async def _reject(request, context: grpc.aio.ServicerContext):
    await context.abort(grpc.StatusCode.UNAUTHENTICATED, f"missing or invalid {API_KEY_HEADER}")


class ApiKeyInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, api_key: str):
        self._api_key = api_key.encode()
        self._reject_handler = grpc.unary_unary_rpc_method_handler(_reject)

    async def intercept_service(self, continuation, handler_call_details):
        metadata = dict(handler_call_details.invocation_metadata or ())
        provided = metadata.get(API_KEY_HEADER, "").encode()
        if hmac.compare_digest(provided, self._api_key):
            return await continuation(handler_call_details)
        log.warning("rejected unauthenticated call to %s", handler_call_details.method)
        return self._reject_handler
