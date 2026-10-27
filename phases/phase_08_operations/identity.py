"""Catalog-based equivalence; source identities never discarded."""
import re
from contracts.operations import ProductIdentity

BAXTER_CATALOG = 'https://catalog.baxter.com/baxterUS/en/Products/Infusion-Therapies-%26-Technologies/IV-Solutions/0-9%25-Sodium-Chloride-Injection%2C-USP%2C-1000-mL-VIAFLEX-Plastic-Container/p/2B1324X'
AUTHORITATIVE_NDC = '0338-0049-04'


def ndc11(value, authoritative=AUTHORITATIVE_NDC):
    """Preserve segment boundaries; bare 11 digits need an explicit catalog anchor."""
    if re.fullmatch(r'[0-9]{11}', value):
        if value != ndc11(authoritative, authoritative):
            raise ValueError('UNRESOLVED_NDC11')
        return value
    parts = value.split('-')
    if tuple(map(len, parts)) not in {(4, 4, 2), (5, 3, 2), (5, 4, 1), (5, 4, 2)} or not all(
        re.fullmatch(r'[0-9]+', p) for p in parts
    ):
        raise ValueError('AMBIGUOUS_OR_FUTURE_NDC')
    normalized = ''.join(p.zfill(n) for p, n in zip(parts, (5, 4, 2)))
    if tuple(map(len, parts)) == (5, 4, 2) and value != authoritative:
        if normalized != ndc11(authoritative, authoritative):
            raise ValueError('UNRESOLVED_NDC11')
    return normalized


def resolve(identity):
    p = ProductIdentity.model_validate(identity)
    original = p.model_dump(mode='json')
    failures = []
    if p.manufacturer_code and p.manufacturer_code != '2B1324X':
        failures.append('MANUFACTURER_CODE_CONFLICT')
    if p.ndc:
        try:
            if ndc11(p.ndc) != ndc11(AUTHORITATIVE_NDC):
                failures.append('NDC_CONFLICT')
        except ValueError as exc:
            failures.append(str(exc))
    if not p.manufacturer_code and not p.ndc:
        failures.append('MISSING_AUTHORITATIVE_IDENTIFIER')
    if (p.strength_mg_ml, p.volume_ml, p.dosage_form, p.container,
        p.manufacturer, p.labeler) != (9, 1000, 'injection', 'viaflex-bag', 'Baxter', 'Baxter'):
        failures.append('INCOMPATIBLE_PRESENTATION')
    # No actual GTIN is retained for this presentation; do not invent one.
    if p.gs1_gtin:
        failures.append('UNVERIFIED_GS1_IDENTIFIER')
    return dict(original=original, canonical_ndc='00338004904' if not failures else None,
                manufacturer_code='2B1324X' if not failures else None,
                classification='unresolved' if failures else 'usable_with_assumptions',
                failures=failures, review_required=bool(failures),
                units_per_case=p.units_per_case, reference=BAXTER_CATALOG,
                limitations=['Pack count is a synthetic item-master assertion, not verified manufacturer packaging.',
                             'Current public catalog is not proof of exact historical catalog custody.'])


def catalog_identity(row):
    """Existing CSV contract provides the reviewed presentation; optional explicit attributes override it."""
    return ProductIdentity(source_organization=row['facility_id'], local_sku=row['local_sku'],
        distributor_item=row.get('distributor_item') or None,
        manufacturer_code=row.get('manufacturer_code') or None, ndc=row.get('ndc') or None,
        name=row.get('name') or 'Baxter Sodium Chloride', strength_mg_ml=int(row['strength_mg_ml']),
        volume_ml=int(row['volume_ml']), dosage_form=row.get('dosage_form', 'injection'),
        container=row.get('container', 'viaflex-bag'), units_per_case=int(row['case_bags']),
        manufacturer=row.get('manufacturer', 'Baxter'), labeler=row.get('labeler', 'Baxter'),
        gs1_gtin=row.get('gs1_gtin') or None, mapping_reference='synthetic-catalog-exact-presentation')


def validate_catalog(row):
    result = resolve(catalog_identity(row))
    if result['failures'] or row['base_unit'] != 'bag' or int(row['total_mg']) != 9000:
        raise ValueError('UNRESOLVED_PRODUCT_IDENTITY')
    return result
