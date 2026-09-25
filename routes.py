"""The 12 Moz endpoints. Every path is served with and without the `/moz`
prefix, so code generated against the hosted API on RapidAPI (paths like
/domains/overview) runs unchanged against this server.

Params are validated by the marshmallow schemas in moz/schemas.py (unknown
params are rejected). Errors: bad params -> 400, page not on moz.com -> 404,
retries exhausted / blocked -> 500.
"""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from moz import domains, mozcast, rankings, schemas
from scraper_errors import BadRequest, NotFound
from schema_fields import load_query


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query as unicode strings (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "münchen.de" would arrive mangled)."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _page_link(path, params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "")}
    query["page"] = page
    host = request.get_header("Host") or "localhost"
    return f"{request.urlparts.scheme}://{host}{path}?{urlencode(query)}"


def paginate(result, path, params):
    """Lift the `pagination` block into flat count / per_page / current_page /
    total_pages / next / previous fields (the hosted API's shape)."""
    pagination = result.pop("pagination", None) or {}
    page = int(pagination.get("page") or 1)
    total_pages = int(pagination.get("total_pages") or 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(path, params, page + 1 if page < total_pages else None),
        "previous": _page_link(path, params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def handle(label, schema, impl, paginated=False):
    raw = query_dict()
    data, error = load_query(schema, raw)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:                # params valid but out of range (page past the end)
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"moz rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:                 # retries exhausted / blocked
        return json_response({"error": f"moz {label} failed: {e}"}, 500)
    if paginated:
        result = paginate(result, request.path, raw)
    return json_response(result)


def mount(path, schema, impl, paginated=False):
    """Serve an endpoint at /path and /moz/path."""
    def handler():
        return handle(path.strip("/"), schema, impl, paginated)
    handler.__name__ = "moz_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/moz" + path, method="GET")(handler)


ENDPOINTS = [
    ("/domains/overview", schemas.DomainSchema, domains.overview, False),
    ("/domains/authority", schemas.DomainSchema, domains.authority, False),
    ("/domains/authority/bulk", schemas.BulkDomainsSchema, domains.bulk_authority, False),
    ("/domains/top-pages", schemas.DomainSchema, domains.top_pages, False),
    ("/domains/top-linking-domains", schemas.DomainSchema, domains.top_linking_domains, False),
    ("/domains/link-history", schemas.DomainSchema, domains.link_history, False),
    ("/rankings/top-domains", schemas.TopListSchema, rankings.top_domains, True),
    ("/rankings/top-brands", schemas.TopListSchema, rankings.top_brands, True),
    ("/google-algorithm-updates", schemas.AlgorithmUpdatesSchema, mozcast.algorithm_updates, True),
    ("/google-algorithm-updates/most-volatile", schemas.EmptySchema, mozcast.most_volatile_days, False),
    ("/mozcast/weather", schemas.WeatherSchema, mozcast.weather, False),
    ("/mozcast/serp-features", schemas.SerpFeaturesSchema, mozcast.serp_features, False),
]

for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
