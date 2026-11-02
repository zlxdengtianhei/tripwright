"""External data providers (transit, weather, geocoding, opening hours).

Contract shared by every provider (see src/tripwright/providers/base.py):
every result carries `source`, `fetched_at` (ISO 8601 with offset) and
`valid_until` (or None), and failures raise ProviderUnavailable with the
upstream reason instead of returning an empty success.
"""
