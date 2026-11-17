# Taking this to a port

How to present the Import Value Checker to a customs or port authority, what to show, what they will ask, and what still has to be built before they can say yes. Written 17 November 2026.

## The one-sentence offer

**In ten seconds at the vehicle, an inspector knows how the factory built the car and what it should be worth, and a supervisor sees every declaration that needs a second look and how much duty rides on it.**

Lead with money and with the officer's time. Do not lead with "AI" or with "fraud detection".

## Who you are talking to, and what each one wants

| Person | What they care about | What to show them |
|---|---|---|
| Director of customs or revenue | Duty collected, and being able to defend decisions | Dashboard: "Estimated duty at stake", referred against cleared |
| Head of valuation | A consistent basis for querying a declared value | Result page: the price bar, "Why this flag?", the source of every figure |
| Inspectors at the gate | Speed, and not typing | Scan a VIN sticker, the answer appears; works on their own phone |
| IT and security | Where the data lives, who can see it | Runs on their own server, no outside service required, audit log |
| Legal | Not breaching valuation rules | The wording: a flag is "needs review", never a finding |

## The demonstration, in five minutes

Fill the app with demonstration data first: `python scripts/seed_demo.py` (remove it later with `--clear`). Set the port's name and duty rate in Settings so the screens carry their name.

1. **The gate (1 minute).** On a phone, scan or type a VIN. Point out: the trim and engine come from the VIN, not from the badges; the expected price is for that exact trim.
2. **A low declaration (1 minute).** Type a declared price well below the expected one. The verdict and the price bar change as you type. Tap "Differs" on one checklist line to raise the separate identity flag. Save.
3. **The record (1 minute).** Show "Why this flag?", the earlier checks of the same seller, and "Print or save as PDF": a one-page record with signature lines.
4. **The supervisor's desk (1 minute).** Open the Dashboard on a laptop: checks per day, what was flagged, sellers that keep needing review, duty at stake. Click a seller to see their declarations.
5. **Control (1 minute).** Settings: their thresholds, their depreciation schedule, their exchange rates. Audit log: who opened which record and when.

## The numbers to quote, and the ones not to

Safe to say, because they were measured:

- The built-in vehicle database returned the right make, model and year for 4,992 of 5,000 real VINs, without internet.
- The built-in price list has about 47,000 manufacturer prices and found a price for 95% of 4,000 real VINs, 83% for the exact model year.
- For current models (2025 to 2027) the app knows the price of each trim for about 300 models.

Do not claim:

- **A detection rate or recovered revenue.** Nothing has run at a real port. "Duty at stake" is an estimate of what is worth reviewing, not money found.
- **Coverage outside the US market.** The vehicle database and almost all prices are for cars built for the US. Ask early what share of their imports that is.
- **Market value.** Today's value is the price when new, reduced by an age table. Say that plainly, and present their own official depreciation schedule as the fix.

## Questions they will ask

**"Can we use this figure as the customs value?"** No, and say so first. Under the WTO Valuation Agreement the declared transaction value stands unless customs has reason to doubt it, and a database value may not simply replace it. The tool gives the officer a documented reason to ask for more evidence. That is its legal role, and it is a selling point: every flag comes with its working shown.

**"Where is our data?"** On their own server if they want it. The app is one container with one database. It needs no outside service: VIN decoding and prices are built in. The online price lookup can be switched off with one setting.

**"What about cars that are not in your list?"** The inspector types the manufacturer's price once and the app remembers it for every later car with that VIN pattern. They can also import their own price list or past declarations as a spreadsheet.

**"Who is accountable?"** Every inspector has an account. The audit log records logins, checks saved and opened, decisions, price changes, settings and exports.

**"What does it cost to run?"** Hosting for one small server. There are no per-lookup fees unless they choose a paid data provider.

## How to propose a pilot

Ask for something small and measurable, not a contract:

- **One lane or one shift, four to six weeks, alongside the current process.** Nothing changes for declarants.
- **What they provide:** two or three inspectors, one supervisor, their depreciation schedule and exchange rates, and ideally a file of past declarations with VIN and declared value.
- **What you measure together:** seconds per check; share of declarations flagged; share of flagged ones the valuation team agreed were worth querying; additional duty assessed on those.
- **The back-test, before the pilot even starts.** If they give you past declarations, run them through the tool and show what it would have flagged. This is the strongest thing you can bring to a second meeting, and it costs them nothing. It needs a small import script that does not exist yet (see below).

## What has to be built before a real port can use it

In order of how soon a port will ask:

1. **Their market's data.** Decide the target market, then source vehicle and price data for it (Gulf, European or Japanese specification cars). Until then the tool is credible only for US-built imports.
2. **Bulk back-test of past declarations** from a spreadsheet, producing the dashboard figures for their own history.
3. **Their language.** The screens are English only. Arabic with right-to-left layout is the likely first request in the Gulf.
4. **Single sign-on and roles.** Today there are two roles, inspector and administrator. A port will want a supervisor role, and logins tied to their staff directory.
5. **Password reset, record retention rules, and backups** they can operate themselves.
6. **A security review** by someone independent, and a check of the rights to the two largest price datasets, before any paid use.
7. **Real-phone testing.** The camera scan has never read a real VIN sticker.

## Ways to charge, later

Keep this for after a pilot has produced a number. Options that fit how customs bodies buy:

- **Annual licence per port**, including data updates. Easiest for them to budget.
- **Per-lane or per-seat**, if they want to start small.
- **Setup and integration fee** for connecting to their declaration system, which is where the real work is.

Avoid a share of recovered duty. It reads as a bounty on declarants, and it gives the tool a stake in flags being upheld.
