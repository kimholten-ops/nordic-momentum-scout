"""Hjernen: lærer av utfallet til hver hendelse (innsidekjøp og klassifiserte
nyheter), simulerer handler med den lærdommen og foreslår nye.

Ærlighetsregler (testet i tests/test_hjerne.py):
- Modellen som vurderer en hendelse med inngang dag D er trent KUN på
  hendelser der utfallet var kjent før D (utgangsdag < D). Ingen lekkasje.
- Den lærer av ALLE hendelser, ikke bare dem den handlet — ellers lærer den
  bare av sine egne valg og blir aldri klokere på det den hoppet over.
- Målet er netto MERavkastning (etter kostnad, minus børsindeksen) over
  HJERNE_HORISONT handelsdager. Et forslag krever forventet verdi over
  HJERNE_MIN_EDGE og en viss sikkerhet.
- Hele simuleringen regnes på nytt ved hver kjøring fra rådata, så den kan
  ikke drive bort fra det dataene sier. Varslede forslag lagres for seg.
"""
