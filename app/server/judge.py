"""Deterministic, server-only checks. Never return the expected value."""
from decimal import Decimal, InvalidOperation, localcontext
import re
import unicodedata


def normalize(text, options):
    text=str(text)
    for option in options:
        if option=='trim':text=text.strip()
        elif option=='upper':text=text.upper()
        elif option=='lower':text=text.lower()
        elif option=='no_space':text=re.sub(r'\s+','',text)
    return text


def judge(check, value):
    if not isinstance(value,str):return False
    kind=check['kind'];expected=check['expected']
    if kind in ('integer','number'):
        text=unicodedata.normalize('NFKC',value).strip()
        if text.endswith('%'):text=text[:-1].strip()
        if kind=='integer':
            try:
                return bool(re.fullmatch(r'[+-]?[0-9]+',text)) and int(text)==expected
            except ValueError:
                return False
        try:
            number=Decimal(text);target=Decimal(str(expected));tol=Decimal(str(check['tolerance']))
            with localcontext() as context:
                # Compare exact bounds, not a rounded subtraction at 28 digits.
                context.prec = 10050
                return number.is_finite() and target-tol <= number <= target+tol
        except (InvalidOperation,ValueError):return False
    if kind=='text':
        return normalize(value,check.get('normalize',[]))==normalize(expected,check.get('normalize',[]))
    if kind=='choice':return value==expected
    if kind=='set':
        options=check.get('normalize',[])
        parts=[x for x in re.split(r'[,，;；\s]+',value) if x]
        return {normalize(x,options) for x in parts}=={normalize(x,options) for x in expected}
    return False
