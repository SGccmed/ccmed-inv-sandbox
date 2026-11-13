"""The pick lists used across the site: inventory locations and manufacturers.

To add, rename or remove a location or manufacturer, change the lists below. Every form picks them up
(they're shown as type-to-search drop-downs; see page_tools.html).

Both lists are fixed: a new value must be one of these (since v1.11 for manufacturers, so a wrong make can't
slip into costing). Records saved earlier keep their old value until someone changes that field.

Locations:
    Loan and Rental also set the stock item's status (On loan / Rental); every other location is Active.
    Repair is one location for now (it may be split into racks later).
Manufacturers: typing one of them, or a common variant ("Cannon", "Acuson", "sonosite"), is saved with the
    spelling below. A make that isn't here (e.g. Mindray) is refused: add it to MANUFACTURERS first.
"""

SHELVES = [f'{row}{n}' for row in 'ABC' for n in range(1, 5)]          # A1 ... C4
STATUS_LOCATIONS = {'Loan': 'On loan', 'Rental': 'Rental'}              # these locations set the status

# Every location, in the order the drop-down shows them.
LOCATIONS = SHELVES + ['T1', 'UG', '2D', 'Loan', 'Rental', 'Repair', 'ACER', 'LEN', 'INT', 'PBO']
# Places a unit can physically sit: everything except Loan and Rental. Used for cores, customer repairs,
# and for technicians (who don't handle loaners or rentals).
PLACES = [loc for loc in LOCATIONS if loc not in STATUS_LOCATIONS]

MANUFACTURERS = ['GE', 'Philips', 'Canon/Toshiba', 'SonoSite', 'BK', 'Siemens/Acuson', 'Samsung']
# Other ways people type the same make. Keys are lower case with spaces, dashes and slashes removed.
MANUFACTURER_ALIASES = {
    'gehealthcare': 'GE',
    'canon': 'Canon/Toshiba', 'toshiba': 'Canon/Toshiba', 'cannon': 'Canon/Toshiba', 'cannontosh': 'Canon/Toshiba',
    'canontosh': 'Canon/Toshiba', 'cannontoshiba': 'Canon/Toshiba',
    'fujifilmsonosite': 'SonoSite', 'fujifilm': 'SonoSite', 'fujisonosite': 'SonoSite',
    'bkmedical': 'BK', 'bkultrasound': 'BK',
    'siemens': 'Siemens/Acuson', 'acuson': 'Siemens/Acuson', 'siemensacu': 'Siemens/Acuson',
    'siemenshealthineers': 'Siemens/Acuson',
    'samsungmedison': 'Samsung',
}


def _key(text):
    return ''.join(ch for ch in str(text or '').lower() if ch.isalnum())


_LOCATION_KEYS = {_key(loc): loc for loc in LOCATIONS}
_LOCATION_KEYS.update({'loaner': 'Loan', 'onloan': 'Loan'})
_MANUFACTURER_KEYS = {_key(m): m for m in MANUFACTURERS}
_MANUFACTURER_KEYS.update(MANUFACTURER_ALIASES)


def location(value):
    """The list's spelling of a typed location ('a1' -> 'A1', 'loan' -> 'Loan'), or None if it isn't one."""
    return _LOCATION_KEYS.get(_key(value))


def location_status(loc):
    """Stock status that goes with a location: On loan, Rental, or Active."""
    return STATUS_LOCATIONS.get(loc, 'Active')


def manufacturer(value):
    """The list's spelling of a typed manufacturer ('cannon' -> 'Canon/Toshiba'), or None if it isn't one."""
    return _MANUFACTURER_KEYS.get(_key(value))


def check_manufacturer(value, current=None):
    """For a form field: (value to save, problem). Blank stays blank; an unchanged old value is kept as it is;
    anything else must be on the list."""
    value = ' '.join(str(value or '').split())
    if not value:
        return '', None
    if current is not None and value == (current or ''):
        return value, None
    found = manufacturer(value)
    if found is None:
        return value, f'"{value}" is not one of the manufacturers. Pick one from the list.'
    return found, None


def register_choices(app):
    app.jinja_env.globals.update(LOCATIONS=LOCATIONS, PLACES=PLACES, MANUFACTURERS=MANUFACTURERS,
                                  MANUFACTURER_ALIASES=MANUFACTURER_ALIASES)
