"""
Countries by ISO 3166 two-letter code, and a lookup from what people write.

The Arbeitnow location rule keeps countries as codes ("DE", "CH") because codes
are what the local model answers most reliably, and they compare without any
spelling trouble. People see and type names; this turns one into the other.

Standard library only.
"""

from __future__ import annotations

import re

NAMES: dict[str, str] = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan",
    "AG": "Antigua and Barbuda", "AL": "Albania", "AM": "Armenia", "AO": "Angola",
    "AR": "Argentina", "AT": "Austria", "AU": "Australia", "AZ": "Azerbaijan",
    "BA": "Bosnia and Herzegovina", "BB": "Barbados", "BD": "Bangladesh",
    "BE": "Belgium", "BF": "Burkina Faso", "BG": "Bulgaria", "BH": "Bahrain",
    "BI": "Burundi", "BJ": "Benin", "BN": "Brunei", "BO": "Bolivia", "BR": "Brazil",
    "BS": "Bahamas", "BT": "Bhutan", "BW": "Botswana", "BY": "Belarus", "BZ": "Belize",
    "CA": "Canada", "CD": "DR Congo", "CF": "Central African Republic",
    "CG": "Republic of the Congo", "CH": "Switzerland", "CI": "Ivory Coast",
    "CL": "Chile", "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CU": "Cuba", "CV": "Cape Verde", "CY": "Cyprus",
    "CZ": "Czech Republic", "DE": "Germany", "DJ": "Djibouti", "DK": "Denmark",
    "DM": "Dominica", "DO": "Dominican Republic", "DZ": "Algeria", "EC": "Ecuador",
    "EE": "Estonia", "EG": "Egypt", "ER": "Eritrea", "ES": "Spain", "ET": "Ethiopia",
    "FI": "Finland", "FJ": "Fiji", "FR": "France", "GA": "Gabon",
    "GB": "United Kingdom", "GD": "Grenada", "GE": "Georgia", "GH": "Ghana",
    "GM": "Gambia", "GN": "Guinea", "GQ": "Equatorial Guinea", "GR": "Greece",
    "GT": "Guatemala", "GW": "Guinea-Bissau", "GY": "Guyana", "HK": "Hong Kong",
    "HN": "Honduras", "HR": "Croatia", "HT": "Haiti", "HU": "Hungary",
    "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India", "IQ": "Iraq",
    "IR": "Iran", "IS": "Iceland", "IT": "Italy", "JM": "Jamaica", "JO": "Jordan",
    "JP": "Japan", "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia",
    "KM": "Comoros", "KN": "Saint Kitts and Nevis", "KR": "South Korea",
    "KW": "Kuwait", "KZ": "Kazakhstan", "LA": "Laos", "LB": "Lebanon",
    "LC": "Saint Lucia", "LI": "Liechtenstein", "LK": "Sri Lanka", "LR": "Liberia",
    "LS": "Lesotho", "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia",
    "LY": "Libya", "MA": "Morocco", "MC": "Monaco", "MD": "Moldova",
    "ME": "Montenegro", "MG": "Madagascar", "MK": "North Macedonia", "ML": "Mali",
    "MM": "Myanmar", "MN": "Mongolia", "MO": "Macau", "MR": "Mauritania",
    "MT": "Malta", "MU": "Mauritius", "MV": "Maldives", "MW": "Malawi",
    "MX": "Mexico", "MY": "Malaysia", "MZ": "Mozambique", "NA": "Namibia",
    "NE": "Niger", "NG": "Nigeria", "NI": "Nicaragua", "NL": "Netherlands",
    "NO": "Norway", "NP": "Nepal", "NZ": "New Zealand", "OM": "Oman", "PA": "Panama",
    "PE": "Peru", "PG": "Papua New Guinea", "PH": "Philippines", "PK": "Pakistan",
    "PL": "Poland", "PR": "Puerto Rico", "PS": "Palestine", "PT": "Portugal",
    "PY": "Paraguay", "QA": "Qatar", "RO": "Romania", "RS": "Serbia", "RU": "Russia",
    "RW": "Rwanda", "SA": "Saudi Arabia", "SC": "Seychelles", "SD": "Sudan",
    "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia", "SK": "Slovakia",
    "SL": "Sierra Leone", "SM": "San Marino", "SN": "Senegal", "SO": "Somalia",
    "SR": "Suriname", "SS": "South Sudan", "SV": "El Salvador", "SY": "Syria",
    "TG": "Togo", "TH": "Thailand", "TJ": "Tajikistan", "TN": "Tunisia",
    "TR": "Turkey", "TT": "Trinidad and Tobago", "TW": "Taiwan", "TZ": "Tanzania",
    "UA": "Ukraine", "UG": "Uganda", "US": "United States", "UY": "Uruguay",
    "UZ": "Uzbekistan", "VA": "Vatican City", "VE": "Venezuela", "VN": "Vietnam",
    "XK": "Kosovo", "YE": "Yemen", "ZA": "South Africa", "ZM": "Zambia",
    "ZW": "Zimbabwe",
}

# Other ways people write them: the country's own name, short forms, and the
# codes that are not ISO. Lower case.
ALIASES: dict[str, str] = {
    "deutschland": "DE", "allemagne": "DE", "germania": "DE",
    "österreich": "AT", "oesterreich": "AT",
    "schweiz": "CH", "suisse": "CH", "svizzera": "CH",
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "britain": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states of america": "US",
    "the netherlands": "NL", "holland": "NL", "nederland": "NL", "niederlande": "NL",
    "frankreich": "FR", "spanien": "ES", "españa": "ES", "espana": "ES",
    "italien": "IT", "italia": "IT", "polen": "PL", "polska": "PL",
    "belgien": "BE", "belgique": "BE", "belgië": "BE", "dänemark": "DK",
    "danmark": "DK", "schweden": "SE", "sverige": "SE", "norwegen": "NO",
    "norge": "NO", "finnland": "FI", "suomi": "FI", "tschechien": "CZ",
    "czechia": "CZ", "česko": "CZ", "luxemburg": "LU", "irland": "IE",
    "portugal": "PT", "griechenland": "GR", "ungarn": "HU", "rumänien": "RO",
    "kroatien": "HR", "türkei": "TR", "türkiye": "TR", "turkiye": "TR",
    "south korea": "KR", "korea": "KR", "republic of korea": "KR",
    "uae": "AE", "emirates": "AE", "ägypten": "EG", "vereinigte staaten": "US",
    "vereinigtes königreich": "GB", "russian federation": "RU",
    "côte d'ivoire": "CI", "cote d'ivoire": "CI", "burma": "MM",
    "slovak republic": "SK", "macedonia": "MK",
}

_BY_NAME = {n.lower(): c for c, n in NAMES.items()}


def name(code: str) -> str:
    """'DE' -> 'Germany'. An unknown code comes back as it was given."""
    return NAMES.get((code or "").upper(), code or "")


def code(text: str) -> str | None:
    """
    A country's code from its name, an alias, or the code itself.
    'Germany', 'Deutschland', 'de' and 'DE' all give 'DE'. None for anything
    that is not a country (a city, 'EU', 'Europe').
    """
    t = re.sub(r"\s+", " ", (text or "").strip()).lower()
    if not t:
        return None
    if len(t) == 2 and t.upper() in NAMES:
        return t.upper()
    return _BY_NAME.get(t) or ALIASES.get(t)


def valid(c) -> bool:
    return isinstance(c, str) and c.upper() in NAMES


# Every name and alias as one pattern, longest first so "United States of
# America" wins over "United States". ISO codes are left out on purpose: in a
# free-text location "IT", "IN" and "DE" are as likely to be words as
# countries. "UK" is an alias, not a code, and is kept.
_NAME_RE = re.compile(
    r"(?<![\w])(" + "|".join(
        re.escape(n) for n in sorted(set(_BY_NAME) | set(ALIASES), key=len, reverse=True)
        if len(n) > 2 or n in ALIASES) + r")(?![\w])", re.I)


def named_in(text: str) -> list[str]:
    """Codes of the countries written out by name in a piece of text, in order."""
    seen: list[str] = []
    for m in _NAME_RE.finditer(text or ""):
        c = code(m.group(1))
        if c and c not in seen:
            seen.append(c)
    return seen


# German places, for "is this posting in Germany?". Moved here from
# rank_ollama.py so the Arbeitnow code can use it without importing the ranker.
_DE_PLACES = re.compile(
    r"\b(?:germany|deutschland|berlin|munich|münchen|hamburg|cologne|köln|"
    r"frankfurt|stuttgart|düsseldorf|dusseldorf|dresden|leipzig|hannover|"
    r"hanover|nuremberg|nürnberg|bremen|essen|dortmund|aachen|heidelberg|"
    r"karlsruhe|mannheim|bonn|münster|munster|bavaria|bayern|hesse|hessen|"
    r"saxony|sachsen|thuringia|brandenburg|baden[- ]württemberg|"
    r"baden[- ]wurttemberg|north rhine[- ]westphalia|rhineland[- ]palatinate|"
    r"lower saxony|schleswig|mecklenburg|saarland)\b", re.I)


def in_germany(location: str) -> bool:
    """Is this posting located in Germany?

    Decides which years ceiling applies. A regional label that merely includes
    Germany ("EMEA", "European Union", "DACH") is NOT Germany for this purpose:
    those are remote-abroad listings and get the looser ceiling, which is the
    whole point of the split.
    """
    return bool(_DE_PLACES.search(location or ""))


