"""Static file serving for the admin panel."""

from starlette.staticfiles import StaticFiles


class PanelStaticFiles(StaticFiles):
    """StaticFiles that makes browsers re-check the panel's HTML.

    Plain StaticFiles sends Last-Modified and no Cache-Control, so a browser
    may reuse a cached index.html by heuristic without asking — and keep
    running the previous build's bundle after the panel is rebuilt. The
    bundles themselves have content-hashed names and can be cached freely.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path in ("", ".") or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache"
        return response
