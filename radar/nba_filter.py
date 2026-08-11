import re

# Precision-first NBA filter. Ambiguous nicknames such as "Heat", "Magic", "Jazz",
# "Nets", "Kings", "Suns" and "Thunder" are only accepted in their full team form.
NBA_TERMS = [
    r'\bnba\b', r'national basketball association',
    r'los angeles lakers', r'\blakers\b',
    r'boston celtics', r'\bceltics\b',
    r'golden state warriors', r'\bwarriors\b',
    r'new york knicks', r'\bknicks\b',
    r'dallas mavericks', r'\bmavericks\b', r'\bmavs\b',
    r'denver nuggets', r'\bnuggets\b',
    r'oklahoma city thunder', r'\bokc thunder\b',
    r'cleveland cavaliers', r'\bcavaliers\b', r'\bcavs\b',
    r'philadelphia 76ers', r'\b76ers\b', r'\bsixers\b',
    r'los angeles clippers', r'\bclippers\b',
    r'phoenix suns',
    r'milwaukee bucks', r'\bbucks\b',
    r'houston rockets', r'\brockets\b',
    r'san antonio spurs', r'\bspurs\b',
    r'memphis grizzlies', r'\bgrizzlies\b',
    r'new orleans pelicans', r'\bpelicans\b',
    r'portland trail blazers', r'trail blazers', r'\bblazers\b',
    r'sacramento kings',
    r'utah jazz',
    r'indiana pacers', r'\bpacers\b',
    r'detroit pistons', r'\bpistons\b',
    r'atlanta hawks', r'\bhawks\b',
    r'charlotte hornets', r'\bhornets\b',
    r'toronto raptors', r'\braptors\b',
    r'brooklyn nets',
    r'chicago bulls', r'\bbulls\b',
    r'miami heat',
    r'orlando magic',
    r'washington wizards', r'\bwizards\b',

    # High-signal star / newsmaker names. Easy to extend later.
    r'lebron james', r'stephen curry', r'\bcurry\b',
    r'luka doncic', r'luka dončić', r'\bdoncic\b', r'\bdončić\b',
    r'nikola jokic', r'nikola jokić', r'\bjokic\b', r'\bjokić\b',
    r'victor wembanyama', r'\bwembanyama\b', r'\bwemby\b',
    r'giannis antetokounmpo', r'\bgiannis\b',
    r'jayson tatum', r'kevin durant', r'\bdurant\b',
    r'shai gilgeous-alexander', r'\bSGA\b',
    r'anthony edwards', r'jalen brunson', r'joel embiid',
    r'james harden', r'demar derozan', r'kyrie irving',
    r'anthony davis', r'damian lillard', r'jimmy butler',
]

OTHER_LEAGUE_TERMS = [
    r'\bwnba\b', r'college basketball', r'\bncaa\b', r'euroleague',
]

NBA_RE = re.compile('|'.join(NBA_TERMS), re.IGNORECASE)
OTHER_LEAGUE_RE = re.compile('|'.join(OTHER_LEAGUE_TERMS), re.IGNORECASE)


def has_nba_signal(title: str, summary: str = '') -> bool:
    return bool(NBA_RE.search(f'{title} {summary}'.strip()))


def is_nba_relevant(title: str, summary: str = '', trusted_nba_feed: bool = False) -> bool:
    text = f'{title} {summary}'.strip()
    if NBA_RE.search(text):
        return True
    # A dedicated NBA RSS feed can contain generic headlines such as "Top free agents left".
    # Trust that feed unless the item clearly signals another league and has no NBA signal.
    if trusted_nba_feed and not OTHER_LEAGUE_RE.search(text):
        return True
    return False
