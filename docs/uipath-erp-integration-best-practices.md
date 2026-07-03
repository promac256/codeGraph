# UiPath Integration Best Practices — ERP/MRP System Landscape

**Scope:** Infor BPCS 8.1 · Infor LX 8.4 · Infor VISUAL 10 & 9.0.8 (2 instances) · Microsoft Dynamics AX 4.0 · Microsoft Dynamics NAV 2016 · SAP ECC 6.0 (EHP4) · SAP Business One v10 · Software Arts PC/MRP · Global Shop Solutions · CRT (custom) · Simone (custom)

**Date:** July 2026
**Method:** Multi-source web research (44 research agents, 22 sources fetched) with adversarial verification of licensing claims, synthesized with UiPath/vendor documentation. Confidence levels are flagged throughout:
- ✅ **Verified** — survived 3-vote adversarial fact-checking against primary sources
- 📄 **Sourced** — from official UiPath/vendor documentation or practitioner reports (not independently verified)
- ⚠️ **To confirm** — general product knowledge; validate with the vendor/your contract before relying on it

---

## 1. Executive Summary

This landscape spans four distinct automation profiles, and the best practice differs sharply by profile:

| Profile | Systems | Recommended primary approach |
|---|---|---|
| IBM i green screen (5250) | BPCS 8.1, LX 8.4 | UiPath **Terminal activities** with field-based identification |
| API-capable | NAV 2016, SAP B1 v10, SAP ECC 6.0 | **API first** (web services / DI API–Service Layer / BAPI), UI automation as fallback |
| Legacy Windows thick client | VISUAL 10 & 9.0.8, AX 4.0, PC/MRP, Global Shop | UI automation with careful framework/selector strategy; read via SQL where safe |
| Custom in-house | CRT, Simone | Discovery first: prefer any API/DB surface the dev team can expose; UI automation last |

**The two findings most likely to change your plan are contractual, not technical:**

1. ✅ **SAP indirect access:** bots driving SAP ECC create licensing liability even with zero new human users. In *SAP UK v Diageo* [2017] EWHC 189 (TCC), SAP claimed **~£54M** because a non-SAP front end accessed SAP — a scenario RPA recreates at scale. Under SAP's Digital Access model, each bot-created document (sales order, invoice, material document…) is chargeable; under legacy named-user contracts the bot itself may need a named user. Settle the licensing position **before** deploying write-bots against ECC 6.0.
2. ✅ **Microsoft multiplexing:** bots do **not** reduce CAL/named-user requirements for AX 4.0 or NAV 2016. Microsoft's guidance is explicit: any user or device whose access is mediated by an automated process still requires a CAL.

Every system a bot touches needs its own license review (✅ verified as a general duty; the specific Infor/PC-MRP/Global Shop terms were not verified — check those EULAs).

---

## 2. Decision Framework: How to Choose the Integration Approach

Apply in order, stopping at the first viable option per process:

1. **Vendor API / web service** — most stable, survives UI changes, fastest, transaction-safe. (NAV 2016 OData/SOAP, SAP B1 DI API/Service Layer, SAP ECC BAPI/RFC, AX 4.0 AIF where configured.)
2. **Direct database — READ ONLY** — for lookups, reconciliation and reporting, querying the ERP's backing SQL database (UiPath Database activities via ODBC/OLE DB) beats screen scraping. 📄 **Never write directly to ERP tables** — business logic, validations, and audit trails live in the application layer; direct writes corrupt data and void support agreements.
3. **Structured UI automation** — native selectors via the best-fitting framework (Default / UI Automation (UIA) / Active Accessibility (AA)), anchored descriptors, keyboard-first navigation.
4. **Terminal emulation** — for 5250 green screens, this *is* the structured option (UiPath Terminal activities expose fields, not pixels).
5. **Image/OCR** — last resort only (Citrix/RDP-only access, owner-drawn controls). Requires strict resolution/font control on the robot machine.

---

## 3. Per-System Guidance

### 3.1 Infor BPCS 8.1 & Infor LX 8.4 (IBM i / AS400, 5250 green screen)

These two share one playbook — LX is the successor to BPCS and both are 5250 screen-driven on IBM i.

**Approach: UiPath Terminal activities (not desktop UI automation of the emulator window).**

- 📄 **Provider choice** is the decisive setup decision (UiPath *Terminal Session* activity):
  - **EHLLAPI via an existing licensed emulator** — IBM Personal Communications (PCOMM) or IBM i Access Client Solutions (ACS); also supported: Attachmate, BlueZone, Micro Focus Reflection. Reuses the emulator and session profiles the site already runs; requires correct EHLLAPI DLL path and session-name (short session ID) configuration.
  - **UiPath Direct Connection** — built-in TN5250 emulation, no third-party emulator needed on the robot machine. Simpler unattended deployment (nothing to install/license on the VM), but test against your specific host configuration.
  - Note: UiPath's legacy "Internal Provider" is **deprecated** in favor of Direct Connection — a known migration pitfall for older workflows.
- 📄 **Field-based identification over coordinates.** UiPath's own mainframe-automation guidance recommends identifying fields by `LabeledBy` / `FollowedBy` / `Index` properties rather than row/column coordinates. BPCS/LX screens are stable, but coordinate-based automation breaks on any screen variant (message lines, different subfile page sizes); field-based survives.
- 📄 **5250-specific keys:** UiPath Terminal activities support `FieldExit`, `Field+`, `Field−` — required for numeric fields in BPCS/LX data entry; plain Tab will not commit numeric fields correctly.
- 📄 **Device names / WorkstationID:** if the IBM i restricts or names device sessions (QAUTOVRT limits, named devices for BPCS session control), set the TN5250 WorkstationID per robot to avoid device-name collisions when multiple bots (or bots + humans) connect concurrently.
- **Wait on screen content, not timers:** synchronize on expected text appearing on screen (function key legends, screen titles like the BPCS program ID in the corner) before sending keys — batch jobs and subfile refreshes on IBM i have variable latency.
- ⚠️ **Consider the non-RPA path for high-volume writes:** BPCS/LX programs are driven by DB2 files and, in many shops, offer batch interfaces/offline entry programs. A nightly file drop processed by a native job can be more robust than a thousand screen conversations — worth asking your IBM i team before scripting a high-volume process.
- **Licensing:** ✅ verify Infor's terms for automated access (open question from research — whether an unattended 5250 bot session counts as a licensed user under your Infor contract).

### 3.2 Infor VISUAL 10 & VISUAL 9.0.8 (two instances)

Windows thick client over SQL Server (or Oracle). No UiPath connector exists; the nearest practitioner reports (Infor client automation generally) confirm it works but needs careful selector tuning.

- ⚠️ **Check the VISUAL API Toolkit first.** Infor offers an API toolkit for VISUAL (used by integrators for order/receipt/labor transactions). If your license includes it, transactional writes through the toolkit are far more robust than UI automation. Confirm availability and coverage for both 9.0.8 and 10 with your Infor partner.
- 📄 **Reads via SQL.** VISUAL's schema is directly queryable; use UiPath Database activities (ODBC/OLE DB, read-only credentials) for lookups, status checks, and reconciliation instead of navigating the client.
- **UI automation strategy for writes without the toolkit:**
  - Try **UI Automation (UIA)** framework first, fall back to **Active Accessibility (AA)** — legacy Windows clients frequently expose better trees under one or the other (UiPath lets you switch per-target with F4 in Studio).
  - 📄 Use **Anchor Base / fuzzy selectors / image-fallback descriptors** for grids and non-unique controls (UiPath's unified target descriptors: fuzzy + anchor + image redundancy).
  - Prefer keyboard navigation (Tab order, accelerator keys) over mouse clicks in VISUAL's data grids — more deterministic across window sizes.
- **Two instances = two environments in code.** Treat 9.0.8 and 10 as separate applications: separate Orchestrator assets/queues per instance, config-driven connection/window titles, and **do not assume selector compatibility across the versions** — validate each workflow against both. Build a shared library only for steps proven identical.
- **Version upgrade risk:** when 9.0.8 is eventually upgraded, UI automations are the first casualty; keeping the automation surface small (SQL reads + toolkit writes where possible) minimizes rework.

### 3.3 Microsoft Dynamics AX 4.0

The hardest Microsoft target in the list: a 2006-era Win32/MorphX client, long out of support, predating any UiPath connector.

- 📄 **Known selector problem:** practitioners report AX client selectors can only be captured at container/block level, not individual fields, under the Default framework — and Studio can freeze extracting selectors. The accepted fix is switching to the **Active Accessibility (AA)** framework, then combining container selectors with **anchors and keyboard navigation** to reach fields.
- **Practical UI pattern:** drive AX forms with keyboard (Ctrl+something accelerators, Tab sequences, grid navigation keys) and use screen text (status bar, record captions) as synchronization points; treat mouse-click-on-field as unreliable.
- ⚠️ **API alternatives that exist in 4.0:** AX 4.0 introduced the **Application Integration Framework (AIF)** and ships the **.NET Business Connector**. If your AX environment has AIF endpoints configured (or your team can configure them), high-volume document flows (orders, journals) are far safer through AIF/Business Connector than through the client UI. This needs an AX developer/administrator — worth it for any process above trivial volume.
- 📄 **Reads via SQL** (read-only) are viable for lookups; AX's data dictionary is well documented.
- ✅ **Licensing:** multiplexing rule applies — bot-mediated access does not reduce CAL requirements, and users benefiting from bot-created data must be licensed.
- **Risk note:** unsupported product + fragile UI automation = keep automations shallow here (data entry/extraction), and document a re-platform assumption: anything built on AX 4.0 UI automation is throwaway when the system is replaced.

### 3.4 Microsoft Dynamics NAV 2016 (Navision)

The easiest API win in the estate.

- 📄 **Web services first.** NAV 2016 natively publishes **Pages via OData and SOAP** and **Codeunits via SOAP** (Microsoft-documented). From UiPath, call these with HTTP Request / SOAP Request activities (or a small .NET wrapper): stable, fast, transaction-safe, and immune to client changes. OData is faster and supports JSON.
  - Publishing a page/codeunit as a web service in NAV is a 5-minute admin task (Web Services table) — do this rather than automate the role-tailored client.
- 📄 **Practitioner consensus:** UI automation of the NAV Windows role-tailored client (RTC) suffers unreliable element detection and selector instability; forum guidance consistently steers to web services or the NAV web client. Treat RTC UI automation as a last resort; if UI is unavoidable, the **web client** (browser automation, stable HTML selectors) beats the RTC.
- 📄 **ODBC/OData driver route** (e.g., CData) is a documented UiPath pattern for read/write against NAV data where a driver is preferred over hand-built HTTP calls.
- ✅ **Licensing:** multiplexing — same as AX; bot access doesn't reduce NAV named-user/CAL needs. NAV 2016 web-service sessions also consume licensed sessions; size accordingly.

### 3.5 SAP ECC 6.0 EHP4

UiPath's most mature ERP tooling — but with the sharpest licensing edge in the whole estate.

- 📄 **Primary approach: SAP WinGUI automation with UiPath's dedicated SAP activities.** UiPath has first-class SAP GUI support with selectors based on SAP's own scripting engine (reliable IDs, not screen coordinates), dedicated SAP activities, and hard-timeout handling for stuck SAP sessions.
- 📄 **Prerequisites (canonical checklist):**
  - Server: enable `sapgui/user_scripting` via RZ11 — with "switch on all servers" in multi-instance landscapes.
  - Client: enable scripting in SAP GUI options (and suppress the notification popups).
  - Authorization: the bot user needs the scripting authorization (auth object `S_SCR`, activity 16/Execute).
  - Performance: set the connection to **High Speed Connection (LAN)** for reliable low-latency scripting.
- 📄 **API alternative: BAPI/RFC.** SAP's own RPA guidance recommends BAPIs (RFC-enabled function modules) when GUI scripting is disabled or when stability/throughput matter; UiPath ships **SAP BAPI activities** alongside WinGUI ones. BAPIs are the right choice for high-volume, headless document creation — but note they are still "indirect use" for licensing purposes.
- **Operational hygiene specific to SAP:** dedicated bot SAP user IDs (never shared human accounts) with minimal roles; use SAP transaction codes typed into the OK-code field for deterministic navigation; handle the modal-dialog patterns (status bar messages are exposed to the scripting engine — read them instead of OCR).
- ✅ **Licensing — the big one:** bot-driven document creation in ECC is chargeable under **Digital Access** (per-document, nine document types) or may require a named-user license for the bot under legacy contracts; *SAP v Diageo* (~£54M claimed) is the cautionary precedent, and SAP audits assert claims under both frameworks. Read-only bots that create none of the chargeable document types may fall outside Digital Access scope. **Engage your SAP licensing/SAM owner before the first unattended write-bot goes live**, and model projected bot document volumes to choose between Digital Access and named-user coverage.

### 3.6 SAP Business One v10

Do **not** default to UI automation here — B1 v10 has a modern API surface.

- 📄 **DI API** (COM-based Data Interface API): server-validated writes with full business-logic enforcement — the standard integration path for documents and master data.
- 📄 **Service Layer** (RESTful, JSON/OData): the modern integration surface; with B1 v10 it is available beyond HANA (⚠️ confirm Service Layer availability for your database platform — SQL Server support arrived in the 10.0 line). From UiPath, this is plain HTTP Request activities — the cleanest possible integration in the whole estate.
- 📄 **UI API is for input capture only** — vendor guidance is that it's always paired with DI API, not used as a standalone automation path.
- If a process genuinely requires the B1 client UI, the client is a comparatively modern Windows app; standard UIA selectors generally work — but there should be very few such processes.
- **Licensing:** ⚠️ B1 licensing is named-user based; a bot session via DI API/Service Layer consumes a user license, and B1 also has specific license types for indirect/integration access — confirm with your SAP B1 partner (this was an open question in research, not a verified finding).

### 3.7 Software Arts PC/MRP

Small vertical MRP package; no connector, no meaningful API.

- ⚠️ **Database route for reads:** PC/MRP historically stores data in xBase/FoxPro-style (.dbf) tables — if so in your version, UiPath can read them via ODBC (Visual FoxPro/dBase driver) for lookups and extracts far more reliably than scraping. Confirm the storage format of your installation and Software Arts' position on external reads.
- 📄 **UI automation:** apply the legacy-Windows-app playbook (see §3.9): try UIA then AA frameworks, keyboard-first driving, anchors, and image/OCR only as final fallback. Expect owner-drawn or non-standard controls in places.
- Keep automated processes here narrow and high-value; the app's small install base means no community selector knowledge exists — everything must be built and regression-tested in-house.
- ⚠️ **Licensing:** check the PC/MRP EULA for automated-access/concurrent-session terms (unverified inference from the general third-party-licensing duty).

### 3.8 Global Shop Solutions

Windows thick-client ERP on a SQL backend; no UiPath connector.

- ⚠️ **Ask about native extension surfaces first:** Global Shop offers its **GAB (Global Application Builder)** scripting/customization environment and vendor-supported integration options — a GAB-side implementation (or vendor-blessed import routine) can be more robust than external UI automation for transactional writes. Confirm what your license includes.
- 📄 **Reads via SQL** (read-only ODBC) for lookups/reporting rather than screen navigation.
- 📄 **UI automation:** same thick-client playbook — UIA/AA framework testing per screen, anchor-based descriptors, keyboard navigation, fuzzy selectors with image fallback for grids.
- ⚠️ **Licensing:** check Global Shop's user/session terms for bot sessions (unverified inference — same duty as above).

### 3.9 CRT and Simone (custom in-house platforms)

Only generic best practices apply — but "custom in-house" is actually an advantage: **you own the code.**

1. **Don't screen-scrape your own software.** The single best practice for in-house platforms is to have the owning dev team expose a stable surface — a small REST endpoint, a stored procedure, a CSV/queue drop — for each process worth automating. Any API you can add is cheaper over its lifetime than a UI automation you must maintain.
2. **If UI automation is unavoidable**, first identify the technology (Win32? WinForms? WPF? Java? Web?) — it dictates everything:
   - Web → browser automation with stable HTML selectors (ask the devs to add `id`/`data-*` attributes — trivial for them, transformative for selector stability).
   - WinForms/WPF → UIA framework; ask devs to set `AutomationProperties`/control names.
   - Java → UiPath Java bridge; needs the Java extension installed on robot machines.
   - Legacy Win32/owner-drawn → AA framework, keyboard driving, anchors, image/OCR fallback.
3. **Stabilize the contract:** agree with the owning team that automated screens/endpoints are change-controlled — the most common RPA failure mode is an unannounced UI tweak breaking production bots (a top-cited RPA program pitfall).
4. Version the automation together with the platform: when CRT/Simone release, run the bot regression suite as part of their release gate.

---

## 4. Cross-Cutting Best Practices (apply to every system above)

### 4.1 The unattended-session trap (top field-reported failure mode)

📄 Automations that work in Studio/attended commonly break unattended because the Robot Service creates an **RDP session with different resolution, scaling, and font smoothing** than the developer's console session. This hits exactly the systems in this estate hardest: terminal emulators, VISUAL, AX thick client, and any image-based fallback.

- Pin robot-VM resolution: set `LoginToConsole` / explicit `Resolution Width/Height/Depth` in robot settings; enable font smoothing if any image/OCR automation exists.
- Develop at the same resolution/scaling (100%) the production robot will use.
- Never leave "it works on my machine" untested: every workflow must pass at least one full unattended run in the production session configuration before go-live.

### 4.2 Robot and credential hygiene

- **One dedicated ERP account per unattended robot** (per system): preserves the ERP audit trail, avoids concurrent-session lockouts (critical on IBM i device names, SAP sessions, NAV session limits), and keeps license accounting clean. 📄 Attended bots necessarily run under the human's own credentials — no security isolation — which is one reason to prefer unattended for regulated processes.
- Store all credentials in **Orchestrator assets/credential store** (or your vault), never in workflows.

### 4.3 Architecture and resilience

- **Queue-based design (REFramework or equivalent):** one queue item per business transaction, with retries, exception screenshots, and idempotency checks (query the ERP before re-posting — double-posting an invoice because of a retry is the classic ERP-RPA incident).
- **Selector strategy:** use the Object Repository so each app version's selectors live in one place (essential for the two VISUAL instances); prefer property-based selectors → anchors → fuzzy → image, in that order.
- **Synchronize on state, not sleeps:** element-exists/text-appears waits everywhere; hard timeouts around host-driven waits (SAP activities have built-in hard-timeout handling for stuck sessions).
- **Environment drift:** dev/test/prod ERP environments differ (patch levels, customizations, screen variants). Test against a copy of production configuration, and inventory ERP patch schedules — 📄 brittle selectors after application updates are a top-cited RPA failure mode.

### 4.4 Licensing and compliance checklist (✅ verified findings)

| # | Finding | Action |
|---|---|---|
| 1 | ✅ SAP indirect access / Digital Access: bot-created ECC documents are chargeable; legacy contracts may require a named user per bot (*SAP v Diageo*, ~£54M claimed) | SAM/licensing review before any ECC write-bot; model document volumes; decide Digital Access vs named-user |
| 2 | ✅ Microsoft multiplexing: bots don't reduce CAL/named-user needs for AX 4.0 / NAV 2016; beneficiaries of bot-created data must be licensed | Count bot-served users in CAL positions for AX and NAV |
| 3 | ✅ UiPath licensing: attended = named-user (tied to username, up to 3 machines); unattended = runtime licenses on the host machine, sized by max concurrency | Size runtimes to concurrent unattended executions across this 11-system estate; expect a mix of attended + unattended |
| 4 | ✅ Duty to verify every touched system's license terms — Infor (BPCS/LX/VISUAL), PC/MRP, and Global Shop specifics were **not** verified | Pull the EULAs; ask each vendor how automated/robotic sessions are counted |

---

## 5. Suggested Prioritization

Balancing integration quality, effort, and risk:

1. **NAV 2016** — publish web services; highest-quality integration for least effort.
2. **SAP B1 v10** — Service Layer / DI API; clean API integration (confirm license treatment).
3. **SAP ECC 6.0** — mature UiPath tooling (WinGUI + BAPI); *gate on licensing review*.
4. **BPCS 8.1 / LX 8.4** — Terminal activities are a well-trodden path; moderate effort, good stability once field-based automation is in place.
5. **VISUAL ×2** — SQL reads early; toolkit-or-UI writes after confirming API Toolkit availability; double validation cost for two versions.
6. **Global Shop / PC/MRP** — SQL/DBF reads first; narrow UI write automations only for clearly high-value processes.
7. **AX 4.0** — hardest and most disposable; automate shallowly, assume replacement.
8. **CRT / Simone** — timeline owned by internal dev capacity; push for API exposure over scraping.

---

## 6. Open Questions (carry into vendor conversations)

From the research's unresolved items:

1. Infor's contractual terms for automated/robotic access to BPCS 8.1, LX 8.4, and VISUAL — does an unattended 5250 or client session count as a licensed user?
2. For your specific IBM i configuration: EHLLAPI-with-site-emulator vs UiPath Direct Connection TN5250 — which is more stable at these versions, and what device-name/session limits apply?
3. Exact usable API surfaces per system: SAP B1 v10 DI API vs Service Layer on your DB platform; NAV 2016 pages/codeunits to publish; VISUAL API Toolkit availability; AX 4.0 AIF configuration state.
4. For ECC 6.0 EHP4's contract vintage: does Digital Access document pricing or a legacy named-user bot license cost less at your projected bot volumes?

## 7. Key Sources

- UiPath Docs — Terminal Session activity (providers, TN5250/EHLLAPI): docs.uipath.com/activities/other/latest/ui-automation/terminal-session
- UiPath KB — Automating Terminals and Mainframes (field-based vs coordinate identification): uipath.com/kb-articles/automating-terminals-and-mainframes
- UiPath Docs — SAP WinGUI configuration steps & About SAP WinGUI Automation: docs.uipath.com
- SAP Help — BAPI overview for RPA: help.sap.com
- Microsoft Learn — Dynamics NAV SOAP and OData web services: learn.microsoft.com/en-us/dynamics-nav/web-services
- Microsoft — Multiplexing licensing guidance; Power Automate licensing FAQ
- ITAM Review — "Robotic Process Automation – licensing dangers" (Dec 2020)
- *SAP UK Ltd v Diageo Great Britain Ltd* [2017] EWHC 189 (TCC)
- UiPath Docs — Robot Windows Sessions (RDP/console, unattended resolution); Anchor Base / descriptor configuration
- UiPath Community Forum — AX selector threads (#126827, #233411), NAV RTC interaction (#104005), AS/400 terminal sessions (#7747, #502844), thick-client best practices (#753456, #249881)
