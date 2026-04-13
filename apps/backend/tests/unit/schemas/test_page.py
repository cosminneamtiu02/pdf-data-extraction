"""Tests for the Page[T] pagination schema."""

from app.schemas.page import Page


def test_page_calculates_pages_correctly():
    """Page should calculate the correct number of pages."""
    # 21 items, 10 per page = 3 pages
    page = Page.create(items=list(range(10)), total=21, page=1, size=10)
    assert page.pages == 3

    # Exact multiple: 20 items, 10 per page = 2 pages
    page = Page.create(items=list(range(10)), total=20, page=1, size=10)
    assert page.pages == 2

    # 1 item, 10 per page = 1 page
    page = Page.create(items=[1], total=1, page=1, size=10)
    assert page.pages == 1


def test_page_zero_total_returns_zero_pages():
    """Page should return 0 pages when total is 0."""
    page = Page.create(items=[], total=0, page=1, size=20)
    assert page.pages == 0
    assert page.items == []
    assert page.total == 0
