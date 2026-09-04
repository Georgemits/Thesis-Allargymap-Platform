"""Analysis services: the parts of AllergyMap that compute rather than serve.

Modules here hold no Flask and no MongoDB code. They take plain documents and
return plain results, so every claim the thesis makes about them can be tested
directly, without a database or a running application.
"""
