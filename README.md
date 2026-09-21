# Overkiz Realtime Position

Echtzeit-Position für Somfy-/Overkiz-Storen in Home Assistant.

Die offizielle [Overkiz-Integration](https://www.home-assistant.io/integrations/overkiz/)
meldet die Position einer Store nur sporadisch – typischerweise alle 15 bis 20
Sekunden und verlässlich erst, wenn die Fahrt beendet ist. Während der Fahrt
springt die Anzeige im Dashboard deshalb in groben Stufen oder gar nicht. Das
ist keine Einstellungssache, sondern eine Eigenschaft der Somfy-Cloud- und
lokalen API (siehe [home-assistant/core#76717](https://github.com/home-assistant/core/issues/76717)).

Diese Custom-Integration legt neben die bestehende Overkiz-Entität eine zweite
Cover-Entität, welche die Position zwischen den Rückmeldungen aus
**Fahrtrichtung × verstrichener Zeit** berechnet und laufend aktualisiert.
Sobald das Gateway eine echte Position meldet, rastet die Berechnung wieder auf
diesen Wert ein – die Anzeige ist also flüssig und driftet trotzdem nicht weg.

## Funktionsprinzip

```
Kommando  ──►  Overkiz-Entität  ──►  Somfy-Gateway  ──►  Motor
   │                  │
   │                  └─ Rückmeldung alle ~20 s, sicher am Fahrtende
   │
   └──►  Rechner: Position = Startposition ± (Zeit × 100 / Fahrzeit)
                  Aktualisierung alle 0.5 s (einstellbar)
```

* **Steuern** – Alle Kommandos werden unverändert an die Overkiz-Entität
  weitergereicht. Die Integration schaltet nichts selbst, sie rechnet nur mit.
* **Einrasten** – Meldet das Gateway das Fahrtende mit einer Position, wird der
  Rechner exakt darauf gesetzt (`position_estimated` wird `false`).
* **Korrigieren** – Kommt während der Fahrt eine Rückmeldung, die stärker als
  die eingestellte Schwelle abweicht, übernimmt der Rechner sie sofort.
* **Mitlesen** – Fahrten, die über eine Funkfernbedienung, die Somfy-App oder
  direkt auf der Overkiz-Entität ausgelöst werden, werden erkannt und
  mitgerechnet.
* **Lernen** – Aus den Rückmeldungen des Gateways wird die tatsächliche
  Fahrzeit laufend nachgeführt (siehe unten).

## Installation

### HACS

1. HACS → Integrationen → ⋮ → **Benutzerdefinierte Repositories**
2. Repository-URL eintragen, Kategorie **Integration**
3. „Overkiz Realtime Position“ installieren
4. Home Assistant neu starten

### Manuell

Den Ordner `custom_components/overkiz_realtime` nach
`<config>/custom_components/overkiz_realtime` kopieren und Home Assistant neu
starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Overkiz Realtime
Position**

1. **Quell-Entität** – die bestehende `cover.*`-Entität aus der
   Overkiz-Integration.
2. **Name** – Name der neuen Entität, vorbelegt mit „<Store> Echtzeit“.
3. **Fahrzeiten** – Dauer einer kompletten Fahrt von ganz geschlossen bis ganz
   offen und umgekehrt. Einmal mit der Stoppuhr messen genügt; auf ±2 s kommt
   es nicht an, den Rest übernimmt die Kalibrierung.
4. **Lamellen** – bei Raffstoren aktivieren und die Kippzeit angeben (meist 1–2 s).

Die neue Entität wird beim gleichen Somfy-Gerät einsortiert wie das Original.
Für den Alltag empfiehlt es sich, im Dashboard nur noch die Echtzeit-Entität zu
verwenden und die originale Overkiz-Entität auszublenden.

## Optionen

Über **Konfigurieren** beim Integrationseintrag:

| Option | Standard | Bedeutung |
| --- | --- | --- |
| Fahrzeit hoch / runter | 25 s | Dauer einer Vollfahrt je Richtung |
| Lamellenposition berechnen | aus | Tilt mitrechnen (Raffstore) |
| Lamellenzeit auf / zu | 1.5 s | Dauer einer kompletten Lamellendrehung |
| Lamellen bei Fahrtbeginn mitdrehen | ein | Abfahrt schliesst die Lamellen, Auffahrt öffnet sie |
| Aktualisierungsintervall | 0.5 s | Takt der Neuberechnung während der Fahrt |
| Kommandoverzögerung | 0 s | Totzeit zwischen Kommando und Anlaufen des Motors |
| Korrekturschwelle | 15 % | Ab dieser Abweichung wird die Gateway-Meldung sofort übernommen; 100 % schaltet die Korrektur während der Fahrt ab |
| Zielposition über Stoppuhr | ein | Für Geräte ohne Positionsunterstützung (RTS): Fahrt wird nach berechneter Zeit gestoppt |
| Fahrzeiten automatisch nachführen | ein | Automatische Kalibrierung |
| Gewicht einer Messung | 0.2 | 1.0 übernimmt jede Messung sofort, kleine Werte glätten über viele Fahrten |

### Zur Korrekturschwelle

Die Rückmeldungen des Gateways sind gegenüber der Realität um ein bis zwei
Sekunden verzögert. Eine zu kleine Schwelle lässt die Anzeige deshalb während
der Fahrt zurückspringen. 15 % fängt grobe Fehler ab, ohne dass es sichtbar
ruckelt.

## Automatische Kalibrierung

Die Integration misst die Fahrzeit **nicht** von „Kommando gesendet“ bis
„Gateway meldet fertig“ – diese Spanne enthält die Latenz von Cloud, Gateway und
Funkstrecke und wäre systematisch zu lang. Stattdessen werden ausschliesslich
die Positionsmeldungen des Gateways gegeneinander gerechnet:

```
gemeldet:  10 % bei t = 2.5 s
gemeldet:  90 % bei t = 22.5 s
           ────────────────────
           80 % in 20 s  →  25 s für 100 %
```

Da beide Meldungen gleich stark verzögert sind, kürzt sich die Latenz heraus.
Eine Messung zählt nur, wenn mindestens 40 % Weg zwischen der ersten und der
letzten Rückmeldung liegen, die Fahrt nicht unterbrochen wurde und das Ergebnis
höchstens 50 % vom bisherigen Wert abweicht. Der neue Wert fliesst gewichtet
ein und wird dauerhaft gespeichert – ein Neustart oder Reload verliert ihn nicht.

Die aktuell verwendeten Werte stehen in den Attributen der Entität
(`travel_time_up`, `travel_time_down`, `last_calibration`,
`calibration_samples`).

Wer es exakt will, ruft einmalig den Dienst `overkiz_realtime.calibrate` auf:
Die Store fährt ganz zu, ganz auf und wieder zu, und die gemessenen Zeiten
werden direkt übernommen.

## Dienste

### `overkiz_realtime.set_known_position`

Setzt die berechnete Position, ohne einen Fahrbefehl zu senden – etwa nachdem
die Store von Hand oder per Fernbedienung verstellt wurde.

```yaml
action: overkiz_realtime.set_known_position
target:
  entity_id: cover.wohnzimmer_echtzeit
data:
  position: 45
  tilt_position: 30
```

### `overkiz_realtime.set_travel_times`

Überschreibt die Fahrzeiten zur Laufzeit und speichert sie dauerhaft.

```yaml
action: overkiz_realtime.set_travel_times
target:
  entity_id: cover.wohnzimmer_echtzeit
data:
  travel_time_up: 23.5
  travel_time_down: 26.0
```

### `overkiz_realtime.calibrate`

Kalibrierfahrt über den kompletten Weg. Setzt voraus, dass die Quell-Entität
eine Position meldet.

```yaml
action: overkiz_realtime.calibrate
target:
  entity_id: cover.wohnzimmer_echtzeit
data:
  direction: both   # both | up | down
```

## Attribute

| Attribut | Bedeutung |
| --- | --- |
| `source_entity_id` | Zugrundeliegende Overkiz-Entität |
| `position_estimated` | `true`, solange die Position gerechnet und nicht vom Gateway bestätigt ist |
| `target_position` | Zielposition der laufenden Fahrt |
| `travel_time_remaining` | Verbleibende Fahrzeit in Sekunden |
| `travel_time_up` / `travel_time_down` | Aktuell verwendete Fahrzeiten |
| `tilt_time_up` / `tilt_time_down` | Aktuell verwendete Lamellenzeiten |
| `last_calibration` | Zeitpunkt der letzten übernommenen Messung |
| `calibration_samples` | Anzahl übernommener Messungen |

## RTS-Motoren ohne Positionsrückmeldung

Reine RTS-Geräte melden überhaupt keine Position. Die Integration wird dann zur
einzigen Positionsquelle:

* Zielpositionen werden zeitgesteuert angefahren – die Integration sendet
  `open`/`close` und nach der berechneten Zeit ein `stop`.
* Eine automatische Kalibrierung ist nicht möglich, da es keine Rückmeldungen
  gibt. Die Fahrzeiten müssen gemessen und eingetragen werden.
* Nach Fahrten per Funkfernbedienung weiss die Integration nichts. In dem Fall
  einmal ganz auf oder ganz zu fahren, oder `set_known_position` verwenden.

## Grenzen

* Wird die originale Overkiz-Entität von aussen auf eine **Zwischenposition**
  gefahren, ohne dass Home Assistant das Kommando sieht (Somfy-App,
  Funkfernbedienung), nimmt die Integration zunächst eine Vollfahrt an und
  korrigiert erst mit der nächsten Rückmeldung. Kommandos, die über Home
  Assistant laufen, werden dagegen erkannt – auch wenn sie direkt an die
  Overkiz-Entität gehen.
* Die Berechnung setzt eine konstante Geschwindigkeit voraus. Anlauf- und
  Bremsrampen sind bei Storen im Bereich weniger Zehntelsekunden und damit
  vernachlässigbar.
* Windautomatik oder Hindernisabschaltung des Motors sieht die Integration erst,
  wenn das Gateway die neue Position meldet.

## Entwicklung

```bash
python -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```

Die Tests laufen gegen eine echte Home-Assistant-Instanz und decken Config-Flow,
Interpolation, Stopp, Resync, externe Fahrten, Lamellen, Kalibrierung und die
Dienste ab.

## Vor dem Veröffentlichen anpassen

In `custom_components/overkiz_realtime/manifest.json` gehören `codeowners`,
`documentation` und `issue_tracker` auf den eigenen GitHub-Account.

## Lizenz

MIT
