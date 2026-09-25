"""Marshmallow request schemas for every /moz/* route.

Generic fields live in the shared top-level schema_fields.py; this module
adds the Moz resolvers and the per-route schemas. Every schema's load()
output is the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrLinkField convention, never a
sibling `url`/`domain` pair):

    domain    hubspot.com | www.hubspot.com | any page link on the site
              | a moz.com/domain-analysis/<domain> link
    domains   up to 10 of the above, comma-separated
    feature   local-packs | featured-snippets | knowledge-graph | videos
              | https-results | reviews | sitelinks
"""
from datetime import date

from marshmallow import ValidationError, fields, validate

from moz import refs
from schema_fields import BaseSchema, ChoiceField, PageField, RefField, StrippedString

MAX_BULK_DOMAINS = 10


class DomainRefField(RefField):
    """`domain`: a domain or any link on it -> the resolved host."""
    resolver = staticmethod(refs.resolve_domain)


class DomainListField(fields.Field):
    """`domains`: comma-separated domains / links -> resolved, deduped list."""

    def __init__(self, max_items=MAX_BULK_DOMAINS, **kwargs):
        kwargs.setdefault("required", True)
        super().__init__(**kwargs)
        self.max_items = max_items

    def _deserialize(self, value, attr, data, **kwargs):
        items = []
        for raw in str(value or "").replace("\n", ",").split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                domain = refs.resolve_domain(raw)
            except ValueError as e:
                raise ValidationError(str(e))
            if domain not in items:
                items.append(domain)
        if not items:
            raise ValidationError("Pass at least one domain (e.g. hubspot.com,semrush.com).")
        if len(items) > self.max_items:
            raise ValidationError(f"At most {self.max_items} domains per request.")
        return items


def _search_field():
    return StrippedString(load_default=None, validate=validate.Length(max=100))


class EmptySchema(BaseSchema):
    pass


# ---- domains ---------------------------------------------------------------------

class DomainSchema(BaseSchema):
    domain = DomainRefField()


class BulkDomainsSchema(BaseSchema):
    domains = DomainListField()


# ---- rankings --------------------------------------------------------------------

class TopListSchema(BaseSchema):
    page = PageField(max_page=10)
    query = _search_field()


# ---- MozCast / algorithm history -----------------------------------------------------

class WeatherSchema(BaseSchema):
    days = fields.Integer(load_default=30, strict=False, validate=validate.Range(min=1, max=90))


class SerpFeaturesSchema(BaseSchema):
    feature = ChoiceField(refs.SERP_FEATURE_SLUGS)
    days = fields.Integer(load_default=30, strict=False, validate=validate.Range(min=1, max=90))


class AlgorithmUpdatesSchema(BaseSchema):
    year = fields.Integer(load_default=None, strict=False,
                          validate=validate.Range(min=2000, max=date.today().year + 1))
    status = ChoiceField(refs.UPDATE_STATUSES)
    query = _search_field()
    page = PageField(max_page=20)
