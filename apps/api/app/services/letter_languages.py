"""Small local language registry: ISO 639-1 codes, English names and MVP aliases.

No model or fuzzy matching is used to interpret input. Unrecognized names fail
closed; this registry is independent of the five Telegram shortcuts.
"""
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


LANGUAGES = dict(line.split(":", 1) for line in """aa:Afar
ab:Abkhazian
ae:Avestan
af:Afrikaans
ak:Akan
am:Amharic
an:Aragonese
ar:Arabic
as:Assamese
av:Avaric
ay:Aymara
az:Azerbaijani
ba:Bashkir
be:Belarusian
bg:Bulgarian
bh:Bihari
bi:Bislama
bm:Bambara
bn:Bengali
bo:Tibetan
br:Breton
bs:Bosnian
ca:Catalan
ce:Chechen
ch:Chamorro
co:Corsican
cr:Cree
cs:Czech
cu:Church Slavic
cv:Chuvash
cy:Welsh
da:Danish
de:German
dv:Divehi
dz:Dzongkha
ee:Ewe
el:Greek
en:English
eo:Esperanto
es:Spanish
et:Estonian
eu:Basque
fa:Persian
ff:Fulah
fi:Finnish
fj:Fijian
fo:Faroese
fr:French
fy:Western Frisian
ga:Irish
gd:Gaelic
gl:Galician
gn:Guarani
gu:Gujarati
gv:Manx
ha:Hausa
he:Hebrew
hi:Hindi
ho:Hiri Motu
hr:Croatian
ht:Haitian
hu:Hungarian
hy:Armenian
hz:Herero
ia:Interlingua
id:Indonesian
ie:Interlingue
ig:Igbo
ii:Sichuan Yi
ik:Inupiaq
io:Ido
is:Icelandic
it:Italian
iu:Inuktitut
ja:Japanese
jv:Javanese
ka:Georgian
kg:Kongo
ki:Kikuyu
kj:Kuanyama
kk:Kazakh
kl:Kalaallisut
km:Central Khmer
kn:Kannada
ko:Korean
kr:Kanuri
ks:Kashmiri
ku:Kurdish
kv:Komi
kw:Cornish
ky:Kirghiz
la:Latin
lb:Luxembourgish
lg:Ganda
li:Limburgan
ln:Lingala
lo:Lao
lt:Lithuanian
lu:Luba-Katanga
lv:Latvian
mg:Malagasy
mh:Marshallese
mi:Maori
mk:Macedonian
ml:Malayalam
mn:Mongolian
mr:Marathi
ms:Malay
mt:Maltese
my:Burmese
na:Nauru
nb:Norwegian Bokmal
nd:North Ndebele
ne:Nepali
ng:Ndonga
nl:Dutch
nn:Norwegian Nynorsk
no:Norwegian
nr:South Ndebele
nv:Navajo
ny:Chichewa
oc:Occitan
oj:Ojibwa
om:Oromo
or:Oriya
os:Ossetian
pa:Panjabi
pi:Pali
pl:Polish
ps:Pushto
pt:Portuguese
qu:Quechua
rm:Romansh
rn:Rundi
ro:Romanian
ru:Russian
rw:Kinyarwanda
sa:Sanskrit
sc:Sardinian
sd:Sindhi
se:Northern Sami
sg:Sango
si:Sinhala
sk:Slovak
sl:Slovenian
sm:Samoan
sn:Shona
so:Somali
sq:Albanian
sr:Serbian
ss:Swati
st:Southern Sotho
su:Sundanese
sv:Swedish
sw:Swahili
ta:Tamil
te:Telugu
tg:Tajik
th:Thai
ti:Tigrinya
tk:Turkmen
tl:Tagalog
tn:Tswana
to:Tonga
tr:Turkish
ts:Tsonga
tt:Tatar
tw:Twi
ty:Tahitian
ug:Uighur
uk:Ukrainian
ur:Urdu
uz:Uzbek
ve:Venda
vi:Vietnamese
vo:Volapuk
wa:Walloon
wo:Wolof
xh:Xhosa
yi:Yiddish
yo:Yoruba
za:Zhuang
zh:Chinese
zu:Zulu""".splitlines())


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


_ALIASES = {_key(name): code for code, name in LANGUAGES.items()}
_ALIASES.update({code: code for code in LANGUAGES})
for code, names in {
    "en": ("английский",), "ru": ("русский",),
    "de": ("немецкий", "Deutsch"), "fr": ("французский", "Français"),
    "es": ("испанский", "Español"), "it": ("итальянский", "Italiano"),
    "pt": ("португальский", "Português"), "hy": ("армянский", "Հայերեն"),
    "pl": ("польский", "Polski"), "ka": ("грузинский", "ქართული"),
    "uk": ("украинский", "Українська"),
}.items():
    _ALIASES.update({_key(name): code for name in names})


def validate_language_code(value: str) -> str:
    if value not in LANGUAGES:
        raise ValueError("Unknown language code")
    return value


LanguageCode = Annotated[str, Field(strict=True, min_length=2, max_length=2), AfterValidator(validate_language_code)]


class CoverLetterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: LanguageCode


class LanguageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(strict=True, min_length=1, max_length=64)


def normalize_language(value: str) -> str:
    code = _ALIASES.get(_key(value)) if 0 < len(value) <= 64 else None
    if code is None:
        raise ValueError("Unknown language")
    return code
