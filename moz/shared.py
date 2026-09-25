"""Helpers shared by the /moz/* list routes."""


def paginate_items(items, page, per_page):
    """Slice one page out of an in-memory list -> (items, pagination block in
    the shape route_glue.paginate lifts into the gateway's flat fields)."""
    total = len(items)
    total_pages = (total + per_page - 1) // per_page
    if total_pages and page > total_pages:
        raise ValueError(f"page {page} is past the last page ({total_pages}).")
    start = (page - 1) * per_page
    return items[start:start + per_page], {
        "page": page,
        "items_per_page": per_page,
        "total_pages": total_pages,
        "total_count": total,
    }
