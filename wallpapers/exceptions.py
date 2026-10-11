"""Uniform error body for the API.

    { "error": { "code": "not_found", "message": "Wallpaper 99 does not exist." } }

DRF exceptions go through ``api_exception_handler``; URLs that match nothing
and unhandled crashes go through ``handler404`` / ``handler500`` so they come
back in the same shape instead of an HTML page.
"""

from django.http import JsonResponse
from django.views.defaults import page_not_found, server_error
from rest_framework.views import exception_handler

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    415: "unsupported_media_type",
    429: "rate_limited",
}


def code_for(status: int) -> str:
    if status >= 500:
        return "server_error"
    return _CODES.get(status, "bad_request")


def error_body(status: int, message: str) -> dict:
    return {"error": {"code": code_for(status), "message": message}}


def _first_message(detail):
    if isinstance(detail, dict):
        return _first_message(next(iter(detail.values()), ""))
    if isinstance(detail, list):
        return _first_message(detail[0]) if detail else ""
    return str(detail)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        response.data = error_body(response.status_code, _first_message(response.data))
    return response


def _is_api(request) -> bool:
    return request.path.startswith("/api/")


def handler404(request, exception=None):
    if _is_api(request):
        return JsonResponse(error_body(404, "Not found."), status=404)
    return page_not_found(request, exception)


def handler500(request):
    if _is_api(request):
        return JsonResponse(error_body(500, "Something went wrong on our side."), status=500)
    return server_error(request)
