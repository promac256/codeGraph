# Unified P2P Finance Platform — Functional Specification & Per-System Integration Touchpoints

**Companion to:** [`uipath-erp-integration-best-practices.md`](./uipath-erp-integration-best-practices.md) (integration-channel rationale, stability practices, licensing constraints)

**Scope decisions (confirmed):**
- **POs stay in the ERPs.** The platform is AP-centric: it *reads* purchase orders and goods receipts from each ERP for 2-way/3-way matching; it never creates or changes POs.
- **Platform pays, ERP records.** The platform executes ACH (NACHA), wire, and check payments directly with the banks, then posts the payment/clearing entries back to each ERP so vendor open items clear in the system of record.

**Confidence flags** (same convention as the companion report):
- 📄 **Sourced/well-established** — standard, widely documented objects (SAP t-codes/BAPIs/tables, NAV page/table numbers, B1 Service Layer entities)
- ⚠️ **To confirm** — version/installation-specific; validate against your installed system's data dictionary or vendor documentation before building

**Date:** July 2026

---

# Part 1 — Exhaustive P2P Functional Decomposition

Each function is a discrete, named capability with an ID so it can become a backlog item. Functions marked **[ERP-touch]** require a per-system integration (detailed in Part 2); everything else is platform-internal.

## 1. Supplier Onboarding (SO)

| ID | Function |
|----|----------|
| SO-01 | Supplier registration invitation (email link, unique token, expiry, resend) initiated by buyer/AP or triggered by first invoice from unknown supplier |
| SO-02 | Guided registration wizard: legal entity data (legal name, DBA, entity type, addresses, jurisdictions) |
| SO-03 | Tax document collection: W-9 (US), W-8BEN/W-8BEN-E (foreign), VAT registration; structured capture + document image retention |
| SO-04 | TIN matching against IRS TIN-match service; VAT number validation (VIES for EU) |
| SO-05 | Banking detail capture: ABA routing + account (ACH), SWIFT/BIC + IBAN (wire), remit-to address (check); routing-number/IBAN checksum validation |
| SO-06 | Bank account ownership verification: prenote (zero-dollar ACH), penny-drop micro-deposit, or third-party account-verification service |
| SO-07 | Sanctions & denied-party screening: OFAC SDN, BIS denied persons, EU/UN consolidated lists; initial screen + ongoing re-screening on list updates |
| SO-08 | Compliance document collection with expiry tracking: insurance certificates (COI), diversity certifications (MBE/WBE/VBE), quality certs (ISO), NDAs/MSAs |
| SO-09 | Duplicate-supplier detection at onboarding: fuzzy match against the unified vendor index built from all 11 ERP vendor masters (normalized name + TIN + bank account + address) |
| SO-10 | Supplier risk scoring: financial health (D&B or similar), sanctions hits, geography, single-source flags |
| SO-11 | Onboarding approval workflow: configurable per entity/category/risk score; segregation of duties (requester ≠ approver ≠ bank-detail verifier) |
| SO-12 | **[ERP-touch]** Vendor master creation/update in the target ERP(s): push approved supplier to each ERP where the supplier will transact (a supplier serving 3 entities on 3 ERPs gets 3 vendor records) |
| SO-13 | Golden-record cross-reference: platform supplier ID ↔ per-ERP vendor numbers (one row per ERP instance, including the two VISUAL instances separately) |
| SO-14 | **[ERP-touch]** Vendor master change management: propagate approved changes (address, terms, bank) to every ERP holding that supplier; detect out-of-band changes made directly in an ERP and flag for reconciliation |
| SO-15 | Supplier deactivation/blocking: propagate holds (quality, compliance, fraud) across all ERPs where the supplier exists |
| SO-16 | 1099 reportability determination and year-end 1099-MISC/NEC data aggregation across all ERPs' payment history |

## 2. Supplier Self-Service Portal (SP)

| ID | Function |
|----|----------|
| SP-01 | Supplier user management: registration, MFA, role-based access (admin vs invoice clerk), multi-user per supplier |
| SP-02 | Profile & contact maintenance: addresses, contacts, order/remit emails — with re-validation workflow before changes take effect |
| SP-03 | Bank-detail change with out-of-band verification: callback to a known contact + re-verification (SO-06) before any payment uses the new account (top B2B fraud vector) |
| SP-04 | Document upload & expiry tracking: replacement W-9s, COIs; automatic expiry reminders and hold-on-expiry rules |
| SP-05 | **[ERP-touch]** PO visibility: supplier sees their open POs (ingested from the ERPs) with line status |
| SP-06 | PO-flip invoice creation: supplier converts a PO into an invoice in the portal (pre-validated against PO lines — highest-quality invoice channel) |
| SP-07 | Non-PO invoice submission with mandatory fields enforced at entry |
| SP-08 | Invoice status inquiry: received → in validation → in approval → approved/scheduled → paid, with reason codes on exceptions (deflects "where's my invoice" calls) |
| SP-09 | Payment status & remittance advice retrieval: payment date, method, amount, invoices covered |
| SP-10 | Dispute / short-pay dialog: structured back-and-forth on rejected or short-paid invoices with document attachments |
| SP-11 | Statement reconciliation: supplier uploads statement; platform auto-matches against invoice/payment ledger and reports gaps |
| SP-12 | Dynamic discounting / early-payment offers: standing or per-invoice offers, sliding-scale discount by acceleration days, approval and recalculated payment schedule |
| SP-13 | Announcements & policy pages: invoicing requirements per entity (PO format, mandatory fields, tax rules) |

## 3. Procurement Management — Visibility Scope (PR)

*(POs remain authored in the ERPs; the platform ingests and normalizes.)*

| ID | Function |
|----|----------|
| PR-01 | **[ERP-touch]** PO ingestion & normalization from all 11 systems into a canonical PO model (header, lines, UoM, currency, terms, delivery schedule, buyer) |
| PR-02 | **[ERP-touch]** PO change/version tracking: detect line changes, price changes, cancellations between syncs; keep version history for match audit |
| PR-03 | **[ERP-touch]** Goods-receipt ingestion: receipts/packing-slip postings per PO line, including partial receipts and returns |
| PR-04 | **[ERP-touch]** PO-line lifecycle status sync: ordered / partially received / received / partially invoiced / fully invoiced / closed — computed from ERP data + platform invoice data |
| PR-05 | Open-PO and un-invoiced-receipt (accrual/GR-IR) reporting across all systems for month-end |
| PR-06 | Contract & rate-card repository: negotiated prices, volume tiers, validity dates — used by validation (VA-08) and match (MA) even where the ERP PO lacks contract linkage |
| PR-07 | Blanket/standing PO handling: releases against blankets, remaining-value tracking, expiry alerts |
| PR-08 | Cross-system spend analytics: spend by supplier/category/entity, maverick (non-PO) spend rate, price-variance trends |
| PR-09 | Buyer workbench: match exceptions routed to the responsible buyer (from PO buyer code) with PO context |

## 4. Multi AP-Inbox Automation (IB)

| ID | Function |
|----|----------|
| IB-01 | Multi-mailbox ingestion: connect N AP mailboxes (per legal entity / AP team / language), Graph/IMAP/OAuth, near-real-time polling or webhooks |
| IB-02 | Email classification (ML/LLM): invoice / credit memo / statement / remittance inquiry / dunning-collections notice / PO acknowledgement / bank-change request / marketing-spam / other — with confidence score and human-review queue below threshold |
| IB-03 | Attachment handling: extract all attachments; classify each (invoice vs backup vs T&Cs); split multi-invoice PDFs into individual documents; merge invoice + supporting docs into one dossier |
| IB-04 | Embedded-content capture: invoices in email body (HTML), links to download portals, password-protected PDFs (flag for manual) |
| IB-05 | Deduplication at intake: hash-based exact dupes (same PDF sent twice, sent to two inboxes) and near-dupe detection before extraction |
| IB-06 | Channel normalization: email, supplier portal (SP-06/07), EDI 810, cXML/PEPPOL e-invoices, paper scan batches, fax-to-email — all land in one intake pipeline with channel provenance |
| IB-07 | Entity routing at intake: determine which legal entity/AP inbox the document belongs to (recipient mailbox, bill-to name/address on document, PO prefix); reroute misdirected mail between inboxes with audit trail |
| IB-08 | Auto-acknowledgement to sender with tracking reference; templated responses for common inquiries (invoice status → link to portal) |
| IB-09 | Non-invoice workflow spawning: statements → SP-11 reconciliation; bank-change emails → SO/SP verification workflow (never auto-applied); dunning notices → escalation to AP lead with the supplier's open items attached |
| IB-10 | Inbox SLA tracking: intake-to-registration cycle time per inbox, backlog dashboards, aging alerts |

## 5. Data Extraction (EX)

| ID | Function |
|----|----------|
| EX-01 | Header extraction (OCR/LLM): supplier identity, invoice number, invoice date, due date, PO number(s), currency, subtotal, tax, freight, misc charges, total, payment terms, bank details on invoice, remit-to |
| EX-02 | Line-item extraction: description, item/part number, quantity, UoM, unit price, line total, line-level PO/line references, tax codes |
| EX-03 | Supplier identification: sender domain + letterhead/logo + TIN + bank details + name fuzzy-match → candidate vendor(s) from the unified vendor index, per target entity/ERP |
| EX-04 | PO-number detection & normalization: per-ERP PO number formats (e.g., SAP 10-digit, NAV alphanumeric `PO-…`, BPCS/LX numeric, VISUAL prefixes) recognized and normalized; multiple POs per invoice supported |
| EX-05 | Tax intelligence: tax type recognition (sales/use/VAT/GST), rate extraction, tax-jurisdiction hints |
| EX-06 | Multi-currency and multi-language extraction; amount/date locale handling (1.000,00 vs 1,000.00) |
| EX-07 | Structured e-invoice parsing (EDI 810, cXML, PEPPOL BIS, UBL) bypassing OCR entirely |
| EX-08 | Confidence scoring per field; auto-pass thresholds configurable per field/supplier |
| EX-09 | Human-in-the-loop verification UI: side-by-side image + fields, keyboard-first correction, low-confidence fields highlighted |
| EX-10 | Extraction learning loop: corrections feed supplier-specific templates/model fine-tuning; per-supplier extraction accuracy tracking |
| EX-11 | Quality fallback queue: handwriting, skewed scans, photos — route to enhanced OCR or manual keying |

## 6. Validation (VA)

| ID | Function |
|----|----------|
| VA-01 | **[ERP-touch]** Vendor-master match & status: resolved supplier exists in the target ERP, is active, not blocked/on-hold; correct company code/entity |
| VA-02 | Supplier contact-info validation: remit-to address vs vendor master; email deliverability/domain checks; phone format; flag mismatches for review rather than auto-accept |
| VA-03 | Financial-info validation: bank details on invoice vs verified vendor-master bank details — **hard stop + fraud workflow on mismatch** (invoice-redirect fraud control); routing/IBAN/SWIFT checksum revalidation |
| VA-04 | Duplicate-invoice detection: exact (vendor + invoice number + amount) and fuzzy (similar number OCR-variants, same amount+date, cross-ERP duplicates for suppliers on multiple systems) against full history |
| VA-05 | Math validation: line sums vs subtotal, subtotal+tax+freight vs total, tax recomputation vs rate |
| VA-06 | Payment-terms validation: invoice terms vs vendor master vs contract (PR-06); flag term degradation (supplier printing Net 15 when contract says Net 45); discount-terms capture (2/10 Net 30) |
| VA-07 | Currency & UoM validation: invoice currency vs PO/vendor default; unit conversions sane |
| VA-08 | Price/contract validation for non-PO invoices against rate cards (PR-06) |
| VA-09 | Tax & withholding compliance: 1099 reportability flags, sales/use tax accrual decision, VAT reverse-charge rules, withholding requirements |
| VA-10 | **[ERP-touch]** PO validity: PO exists, is open/released, not fully invoiced, supplier on invoice = supplier on PO, entity matches |
| VA-11 | Policy validations: invoice age limits, minimum documentation per category, blocked-commodity rules |
| VA-12 | Validation outcome routing: auto-pass → matching; soft fail → AP review queue with reason codes; hard fail → structured supplier rejection (via portal/email) with resubmission guidance |

## 7. Matching (MA)

| ID | Function |
|----|----------|
| MA-01 | Match-strategy determination per invoice: 3-way (invoice↔PO↔GR) when the PO line is receipt-required; 2-way (invoice↔PO) for services/non-stock; non-PO → coding & approval (CA) |
| MA-02 | Line pairing engine: invoice lines ↔ PO lines by item number / description similarity / price; UoM and pack-size conversion (invoice in EA, PO in CS); many-to-one and one-to-many line mappings |
| MA-03 | 2-way match: unit price, extended price, quantity vs PO line, terms vs PO terms, within tolerance |
| MA-04 | 3-way match: invoiced quantity vs received-not-yet-invoiced quantity per PO line (consuming receipts FIFO), price vs PO |
| MA-05 | Configurable tolerances: percentage and absolute, at header and line level, per entity/category/supplier (e.g., ±2% or $25 price; qty exact) |
| MA-06 | Partial handling: partial deliveries, partial invoicing, cumulative match state per PO line across multiple invoices |
| MA-07 | Freight/tax/surcharge handling in match: planned vs unplanned delivery costs, allocation rules |
| MA-08 | Match-exception workflows: price variance → buyer (PR-09); quantity/no-receipt → receiver/warehouse with "confirm receipt" action; wrong-supplier/wrong-PO → AP |
| MA-09 | GR/IR aging: receipts awaiting invoice, invoices awaiting receipt, with aging buckets and escalation |
| MA-10 | Auto-match analytics: touchless rate, first-pass match rate by supplier/entity/ERP, top exception reasons |

## 8. Coding & Approval (CA)

| ID | Function |
|----|----------|
| CA-01 | **[ERP-touch]** Chart-of-accounts/dimension sync from each ERP (GL accounts, cost centers, departments, projects) so coding is valid for the target system |
| CA-02 | ML-suggested GL/cost-center coding for non-PO invoices from supplier/description/history; per-line coding; allocation splits (%, amount, across dimensions) |
| CA-03 | Delegation-of-authority matrix: approval chains by entity, amount, category, cost center; out-of-office delegation; escalation timers |
| CA-04 | Approval experience: email/mobile one-tap approve with invoice image and match context; bulk approval with guardrails |
| CA-05 | Segregation-of-duties enforcement: coder ≠ approver; approver ≠ payment releaser; vendor-master editor ≠ invoice approver |
| CA-06 | Full audit trail: every field change, approval, rejection, delegation with actor/timestamp/before-after |

## 9. Invoice Entry to ERP with Intelligent Routing (IE)

| ID | Function |
|----|----------|
| IE-01 | Target-system routing: legal entity (from IB-07/EX) → routing table → ERP instance + company code/database + adapter + posting variant (see Part 2 routing table) |
| IE-02 | **[ERP-touch]** PO-invoice posting via each ERP's recommended channel (Part 2), carrying PO/GR references so the ERP's own invoice-to-PO linkage is preserved |
| IE-03 | **[ERP-touch]** Non-PO invoice posting with platform-approved GL coding |
| IE-04 | **[ERP-touch]** Credit memo / debit note posting |
| IE-05 | Idempotent posting protocol: platform invoice ID as external reference in the ERP; duplicate-post prevention on retry |
| IE-06 | Posting-error handling: parse ERP error (validation, locked record, session down), classify retryable vs data-fix, retry queue with backoff, dead-letter queue with AP alerting |
| IE-07 | Posted-document capture: ERP document number(s) stored on the platform invoice; deep links/t-code references for auditors |
| IE-08 | **[ERP-touch]** Status sync-back: detect ERP-side blocks, holds, reversals or manual edits made after posting; reconcile platform status |
| IE-09 | Reversal/cancellation propagation: platform-initiated cancellations post the correct reversal document per ERP |
| IE-10 | Posting calendar/period control: respect each ERP's open posting periods; queue for next period or route for period-decision |

## 10. Payments — Platform Executes, ERP Records (PY)

| ID | Function |
|----|----------|
| PY-01 | **[ERP-touch]** Due-item aggregation: approved, posted invoices from all ERPs (open vendor items) into one payment workbench, deduplicated against platform records |
| PY-02 | Payment proposal generation: by due date, discount capture optimization (pay on discount date when 2/10-type terms), supplier netting (invoices − credits), pay-group and entity bank account rules |
| PY-03 | Cash-requirements forecasting across all entities/ERPs; funding alerts per disbursement account |
| PY-04 | Payment-method determination: supplier preference, amount thresholds (e.g., wires > $X), urgency, country/currency → ACH / wire / check |
| PY-05 | Payment approval workflow: separate from invoice approval (CA-05), dual release above thresholds, callback verification for first payment to new bank details |
| PY-06 | ACH execution: NACHA file generation (CCD/CTX with addenda) or bank API; same-day ACH option; batch balancing and control totals |
| PY-07 | Wire execution: ISO 20022 pain.001 / MT101 / bank-portal API; IBAN/BIC validation; cutoff-time awareness; fee handling (OUR/SHA/BEN) |
| PY-08 | Check execution: check print file with MICR, check-stock and signature controls, **positive-pay file** to bank, outsourced check-print option |
| PY-09 | Remittance advice: email/portal delivery, CTX addenda, invoice-level detail for suppliers to auto-apply cash |
| PY-10 | Bank acknowledgement & exception processing: file ack, ACH returns (R-codes) and NOCs (auto-update vendor bank data with verification), wire confirmations/rejections |
| PY-11 | **[ERP-touch]** Payment & clearing posting back to each ERP: payment document created, applied to the specific vendor open items (so the ERP's vendor ledger clears), bank/cash GL account per entity |
| PY-12 | Void / reissue / stop-payment: bank stop request, ERP reversal of the payment document, reissue workflow |
| PY-13 | Escheatment: stale-check tracking, due-diligence letters, state unclaimed-property reporting |
| PY-14 | FX handling: pay-currency vs invoice-currency conversion, rate capture, realized-gain/loss data for ERP posting |
| PY-15 | Bank-statement payment reconciliation: cleared payments vs issued (check clearing, ACH settlement) feeding PY-12/13 |
| PY-16 | Payment analytics: DPO, discount capture rate, method mix/cost, payment-error rate |

## 11. Cross-Cutting Platform Services (XC)

| ID | Function |
|----|----------|
| XC-01 | **[ERP-touch]** Adapter framework: one adapter per ERP instance (12 total — the two VISUAL instances count separately) implementing a common contract: vendor upsert, vendor read, PO read, GR read, invoice post, payment post, open-items read, CoA read — over the channel recommended in the companion report |
| XC-02 | Canonical data model: unified Supplier, PO, Receipt, Invoice, Payment entities with per-ERP cross-reference keys and source-system provenance on every record |
| XC-03 | Sync engine: scheduled + event-driven sync per adapter with watermarking (changed-since), conflict detection, and reconciliation reports (platform vs ERP counts/amounts) |
| XC-04 | Routing table administration: legal entity → ERP instance → company code/DB → adapter → posting rules (Part 2.12) |
| XC-05 | Cross-system supplier deduplication service: match key = normalized name + TIN + bank account (+ address); merge/link workflow building golden records over the 11 vendor masters |
| XC-06 | Controls & audit: SOX-ready immutable audit log, SoD rule engine (CA-05, PY-05), periodic access reviews, control-evidence reports |
| XC-07 | Fraud analytics: bank-change velocity, first-time-supplier + rush-payment combinations, invoice-pattern anomalies, duplicate-across-ERP payments |
| XC-08 | Document archive: original emails, invoice images, approval evidence, remittances — retention policies per jurisdiction, legal hold |
| XC-09 | Dashboards & KPIs: touchless rate, cycle time (receipt→post, post→pay), exception rates by reason, per-ERP adapter health |
| XC-10 | Platform user/role management, SSO, per-entity data visibility |
| XC-11 | Public API + webhooks/event bus so the adapters (and future systems) integrate loosely; replayable event log |
| XC-12 | Adapter operations: UiPath Orchestrator queue integration for the RPA-channel adapters (BPCS/LX terminal, VISUAL/AX/PC-MRP/GSS UI), with retry semantics, screenshot-on-failure, and the unattended-session controls from the companion report §4.1 |

---

# Part 2 — Per-System Integration Touchpoints

Seven integration surfaces per system: **(a) Vendor master read/write · (b) Vendor bank & terms · (c) PO read · (d) GR read · (e) Invoice post · (f) Payment/clearing post · (g) Open-items & status read.**

Channel abbreviations: **API** = native API/web service · **SQL** = read-only database access · **TERM** = UiPath Terminal (5250) · **UI** = UiPath UI automation.

## 2.1 SAP ECC 6.0 EHP4 — channel: BAPI/RFC preferred, SAP GUI scripting fallback 📄

> ✅ Licensing gate: every bot-posted document is Digital Access exposure (companion report §3.5, §4.4). Model volumes before go-live.

| Surface | BAPI/RFC & IDoc (preferred) | GUI t-codes (fallback) | Tables (read/validation via RFC_READ_TABLE or extracts) |
|---|---|---|---|
| (a) Vendor master | `BAPI_VENDOR_CREATE` / `BAPI_VENDOR_EDIT` ⚠️ (limited fields; `CREMAS` IDoc or batch-input on XK01/XK02 is the common full-coverage path) | XK01 create, XK02 change, XK03 display | LFA1 (general), LFB1 (company code), LFM1 (purchasing org) |
| (b) Bank & terms | via CREMAS segments / vendor BAPI extensions | XK02 (payment transactions view) | LFBK (bank details), T052 (terms of payment), LFB1-ZTERM |
| (c) PO read | `BAPI_PO_GETDETAIL1`, `BAPI_PO_GETITEMS` | ME23N display | EKKO (header), EKPO (lines), EKBE (PO history: GR/IR per line) |
| (d) GR read | `BAPI_GOODSMVT_GETITEMS`; EKBE movement type 101/102 | MIGO display, MB03 | MKPF/MSEG (material documents), EKBE |
| (e) Invoice post | **`BAPI_INCOMINGINVOICE_CREATE`** (LIV, PO-based — carries PO/GR refs, 3-way match state) ; `BAPI_ACC_DOCUMENT_POST` (FI, non-PO) ; `INVOIC01/02` IDoc alternative | MIRO (LIV entry), MIR7 (park), FB60 (non-PO), FB65 (credit memo), MIR4 (display) | RBKP/RSEG (LIV docs), BKPF/BSEG (FI docs) |
| (f) Payment/clearing post | `BAPI_ACC_DOCUMENT_POST` with vendor + bank lines, or posting-with-clearing via batch-input F-53/FB05 ⚠️ (BAPI does not clear; clearing typically needs F-53/FB05 or `POSTING_INTERFACE_CLEARING`) | F-53 (post outgoing payment w/ clearing), FB05 (post with clearing), FCH5 (assign check number) | PAYR (check register), BSAK (cleared items) |
| (g) Open items / status | `BAPI_AP_ACC_GETOPENITEMS` | FBL1N (vendor line items), FK10N (balances) | BSIK (open), BSAK (cleared), BKPF |

Notes: F110 (SAP payment run) intentionally **not used** — platform pays (PY) and records via (f). Reversals: `BAPI_ACC_DOCUMENT_REV_POST` / MR8M (LIV cancel), FB08 (FI reversal). Duplicate check config: LFB1-REPRF flag.

## 2.2 SAP Business One v10 — channel: Service Layer (REST) / DI API 📄

| Surface | Service Layer entity (DI API object) | Tables (read) | Client screens (fallback only) |
|---|---|---|---|
| (a) Vendor master | `BusinessPartners` with CardType `cSupplier` | OCRD, CRD1 (addresses) | Business Partner Master Data |
| (b) Bank & terms | `BusinessPartners/BPBankAccounts`; `PaymentTermsTypes` | OCRB (BP bank accounts), OCTG (terms) | BP Master Data → Payment Terms/Payment System tabs |
| (c) PO read | `PurchaseOrders` | OPOR/POR1 | Purchase Order |
| (d) GR read | `PurchaseDeliveryNotes` (GRPO) | OPDN/PDN1 | Goods Receipt PO |
| (e) Invoice post | `PurchaseInvoices` (base-document links to PO/GRPO lines preserve match chain); `PurchaseCreditNotes` for credits | OPCH/PCH1; ORPC/RPC1 (credits) | A/P Invoice, A/P Credit Memo |
| (f) Payment/clearing post | `VendorPayments` (Outgoing Payments) with invoice applications; `PaymentsDrafts` if approval-in-B1 needed | OVPM/VPM2 | Outgoing Payments |
| (g) Open items / status | `PurchaseInvoices` filtered on `DocumentStatus`/open amount; `JournalEntries` | OPCH (DocStatus, PaidToDate), JDT1 | BP account balance |

Notes: ⚠️ confirm Service Layer availability on your DB platform (SQL Server support arrived in the 10.0 line); DI API covers everything regardless. B1 named-user licensing for the integration user — confirm the correct license type for indirect access (companion report §3.6).

## 2.3 Microsoft Dynamics NAV 2016 — channel: OData/SOAP web services 📄

Publish these as web services (Web Services table — 5-minute admin task per object):

| Surface | Pages/Codeunits to publish | Tables behind them (read via OData/queries) |
|---|---|---|
| (a) Vendor master | Page 26 *Vendor Card* (SOAP create/update; OData read) | Table 23 Vendor |
| (b) Bank & terms | Page 424 *Vendor Bank Account Card* ⚠️ (confirm page no. in your build); Payment Terms list | Table 288 Vendor Bank Account, Table 3 Payment Terms |
| (c) PO read | Page 50 *Purchase Order* (read-only use) | Tables 38/39 Purchase Header/Line |
| (d) GR read | Pages 145/146 *Posted Purchase Receipts* | Tables 120/121 Purch. Rcpt. Header/Line |
| (e) Invoice post | Page 51 *Purchase Invoice* to create; post via a small published codeunit wrapping **Codeunit 90 Purch.-Post** (recommended) — returns posted doc no. | Posted: Tables 122/123 Purch. Inv. Header/Line |
| (f) Payment/clearing post | Page 256 *Payment Journal* lines with `Applies-to Doc. No.`, posted via codeunit wrapping **Codeunit 13/12 Gen. Jnl.-Post** — application clears the vendor ledger | Table 81 Gen. Journal Line; Table 25 Vendor Ledger Entry (applications) |
| (g) Open items / status | OData query on Vendor Ledger Entries (Open = true) | Table 25 Vendor Ledger Entry, Table 380 Detailed Vendor Ledg. Entry |

Notes: ✅ multiplexing — bot-served users still need NAV licenses (companion report §4.4). Web-service sessions consume licensed sessions; pool and throttle in the adapter.

## 2.4 Microsoft Dynamics AX 4.0 — channel: AIF / .NET Business Connector if configured; else AA-framework UI automation + SQL reads ⚠️

| Surface | API path (if AIF/BC configured) ⚠️ | UI forms (AA framework + keyboard) | Tables (SQL read-only) |
|---|---|---|---|
| (a) Vendor master | AIF vendor service / Business Connector X++ calls | Accounts Payable → Vendors form | VendTable |
| (b) Bank & terms | Business Connector | Vendors → Setup → Bank accounts; Payment terms | VendBankAccount ⚠️ (BankAccountTable naming varies), PaymTerm |
| (c) PO read | AIF PurchaseOrder document service ⚠️ (AX 4.0's AIF document coverage is narrow — verify) | AP → Purchase Order form (read) | PurchTable, PurchLine |
| (d) GR read | — (SQL preferred) | Posted packing slips inquiry | VendPackingSlipJour/Trans |
| (e) Invoice post | Business Connector into invoice journal | AP → Journals → **Invoice Journal** (LedgerJournalTable/Trans, PO-invoice via posting routines) | VendInvoiceJour (posted), VendTrans |
| (f) Payment/clearing post | Business Connector into payment journal with settlement | AP → Journals → **Payment Journal**, settle open transactions (marks VendTransOpen) | LedgerJournalTrans, VendTrans/VendTransOpen, SpecTrans (settlement marking) |
| (g) Open items / status | SQL | Vendor transactions inquiry | VendTrans + VendTransOpen |

Notes: keep automation shallow — AX 4.0 is the most disposable target (companion report §3.3); known selector problem → AA framework + anchors + keyboard. ✅ Multiplexing applies. Treat every write here as candidate for the invoice-journal path (journals tolerate automation better than the PO-invoice posting form).

## 2.5 Infor BPCS 8.1 & 2.6 Infor LX 8.4 — channel: UiPath Terminal activities (5250); DB2 reads via ODBC ⚠️

One playbook, two systems; ⚠️ **all program/file names below are typical BPCS/LX naming — confirm against your installed data dictionary (DSPFD/DSPFFD or your IBM i team) before building.**

| Surface | Green-screen programs (TERM writes) ⚠️ | DB2 files (SQL reads) ⚠️ |
|---|---|---|
| (a) Vendor master | ACP module vendor-master maintenance (ACP100-series in many installs) | AVM (vendor master) |
| (b) Bank & terms | Vendor master additional views; terms codes maintenance | AVM fields + terms code file |
| (c) PO read | PUR module PO inquiry (PUR300/PUR500-series display) — prefer SQL | PO header/detail files (HPH/HPD in LX; POH/POD naming in some BPCS installs) |
| (d) GR read | Receipt inquiry — prefer SQL | Receipt history file (HPR or ITH transaction history with PO receipt type) |
| (e) Invoice post | **ACP invoice entry conversation (ACP500-series)** — the primary TERM automation; batch header + invoice header + GL/PO distribution screens | APH/APD open payables after post (verify names) |
| (f) Payment/clearing post | ACP manual/quick payment entry conversation (record platform-issued payment against open items) ⚠️ — if a native payment-interface batch file exists, prefer it | Check register / payment history files |
| (g) Open items / status | ACP open-item inquiry — prefer SQL | AP open items file (APH), payment history |

Notes: field-based terminal identification, `FieldExit` for numerics, per-robot WorkstationIDs (companion report §3.1). ⚠️ Ask the IBM i team about native batch/offline interfaces (input files processed by native jobs) for invoice and payment posting — a file drop beats a thousand screen conversations at volume. ✅ Confirm Infor's terms for automated sessions.

## 2.7 Infor VISUAL 10 & 2.8 VISUAL 9.0.8 (two instances) — channel: API Toolkit if licensed; SQL reads; UI fallback ⚠️

⚠️ **Table/screen names are the commonly documented VISUAL schema — verify per instance and per version; maintain two separate adapter configs and cross-reference maps.**

| Surface | API Toolkit (if licensed) ⚠️ | Screens (UI fallback) | Tables (SQL reads) ⚠️ |
|---|---|---|---|
| (a) Vendor master | Financials/Purchasing toolkit components | Vendor Maintenance | VENDOR |
| (b) Bank & terms | — | Vendor Maintenance (terms, remit-to) | VENDOR terms/remit columns; bank data often external ⚠️ |
| (c) PO read | Purchasing toolkit | Purchase Order Entry (read) | PURC_ORDER, PURC_ORDER_LINE |
| (d) GR read | — (SQL) | Purchase Receipt Entry (read) | RECEIVER, RECEIVER_LINE |
| (e) Invoice post | Financials toolkit A/P invoice component ⚠️ (coverage varies by version — the deciding factor for this adapter) | **A/P Invoice Entry** (voucher entry, PO-linked lines for 3-way) | PAYABLE, PAYABLE_LINE (+ PAYABLE_DIST ⚠️) |
| (f) Payment/clearing post | Financials toolkit payment component ⚠️ | **A/P Payment Entry** (record platform-issued ACH/wire/check, apply to vouchers) | CASH_DISBURSEMENT ⚠️ (verify payment tables) |
| (g) Open items / status | SQL | Vendor open payables inquiry | PAYABLE (open amount/status) |

Notes: validate every workflow against **both** instances — no selector reuse assumed between 9.0.8 and 10 (companion report §3.2). Reads always via SQL; UI only for the two write surfaces.

## 2.9 Software Arts PC/MRP — channel: DBF/ODBC reads; keyboard-driven UI writes ⚠️

⚠️ **PC/MRP is xBase/FoxPro-table based in most versions — confirm storage format and exact table names for your installation; module names below are PC/MRP's standard menu structure.**

| Surface | Module/screen (UI writes) | Data (ODBC/DBF reads) ⚠️ |
|---|---|---|
| (a) Vendor master | Modules → Address Book (vendor type entries) | Address book table |
| (b) Bank & terms | Address Book (terms field); bank data typically not stored — platform is system of record ⚠️ | — |
| (c) PO read | Modules → Purchasing (read) — prefer DBF | PO table(s) |
| (d) GR read | Modules → Receiving — prefer DBF | Receiving/inventory-transaction table |
| (e) Invoice post | Modules → Accounting → **Accounts Payable** (enter/edit AP invoice against receiver/PO) | AP table after post |
| (f) Payment/clearing post | Accounting → AP → check/payment entry (record platform payment; PC/MRP check printing not used) | Check-register table |
| (g) Open items / status | AP aging report — prefer DBF | AP open-items table |

Notes: legacy-Windows playbook (UIA→AA, keyboard-first, image fallback last); no community selector knowledge exists — build a full regression suite. ⚠️ Check the EULA for automated-access terms.

## 2.10 Global Shop Solutions — channel: GAB scripts / vendor import routines preferred; SQL reads; UI fallback ⚠️

⚠️ **Confirm table names via the GSS data dictionary and what your license includes (GAB — Global Application Builder — and any vendor-supported import/API options).**

| Surface | GAB / vendor route (preferred) ⚠️ | Screens (UI fallback) | SQL reads ⚠️ |
|---|---|---|---|
| (a) Vendor master | GAB script or vendor import | Vendor Maintenance | Vendor table (via data dictionary) |
| (b) Bank & terms | GAB | Vendor Maintenance (terms/remit) | Vendor terms columns |
| (c) PO read | — (SQL) | PO Entry/Inquiry (read) | PO header/line tables |
| (d) GR read | — (SQL) | PO Receiving (read) | Receipt tables |
| (e) Invoice post | **GAB-side AP invoice import** (ask GSS — most robust) | AP Invoice Entry (voucher w/ PO match) | AP invoice tables after post |
| (f) Payment/clearing post | GAB payment-recording script | AP Check/Payment Processing (record external payment, apply to vouchers) | Check register tables |
| (g) Open items / status | SQL | AP aging inquiry | AP open tables |

Notes: engage GSS support on sanctioned import routines before building UI automation for (e)/(f). ⚠️ Check user/session license terms for bot sessions.

## 2.11 CRT & Simone (custom in-house) — channel: dev-team-exposed endpoints

This subsection doubles as the **requirements hand-off to the internal dev teams**. Required endpoint contract (REST/JSON suggested; DB views acceptable for the read surfaces):

| Surface | Required endpoint | Contract essentials |
|---|---|---|
| (a) Vendor master | `PUT /vendors/{externalId}` upsert; `GET /vendors` delta by `changedSince` | Platform supplier ID stored as external key; validation errors as structured codes |
| (b) Bank & terms | Included in vendor payload **+ webhook on any bank change made inside the app** | Bank changes made natively must notify the platform for fraud re-verification (SP-03) |
| (c) PO read | `GET /purchase-orders?changedSince=` | Canonical fields: lines, UoM, price, open qty, buyer, terms |
| (d) GR read | `GET /receipts?changedSince=` | Receipt lines keyed to PO lines, incl. returns |
| (e) Invoice post | `POST /ap-invoices` with **Idempotency-Key** header | PO/GR line references; returns internal document ID; structured rejection reasons |
| (f) Payment/clearing post | `POST /ap-payments` with applications array (invoice IDs + amounts) | Must clear open items atomically; returns payment doc ID |
| (g) Open items / status | `GET /ap-open-items`; `GET /ap-invoices/{id}/status` | Status enum incl. blocked/held/reversed for IE-08 sync-back |

Notes: change-control the contract (versioned API); bot/UI automation against CRT/Simone should be a temporary bridge at most (companion report §3.9).

## 2.12 Routing Table Template (IE-01 / XC-04)

One row per legal entity; drives intake routing, posting, and payment recording:

| Legal entity | AP inbox(es) | ERP instance | Company code / DB / environment key | Adapter & channel | Invoice posting variant | Payment recording variant | Disbursement bank accounts | Currency(ies) |
|---|---|---|---|---|---|---|---|---|
| e.g. *Acme Manufacturing US* | ap-us@… | SAP ECC 6.0 | company code 1000 | SAP-RFC | BAPI_INCOMINGINVOICE_CREATE | F-53 batch-input | Bank A (ACH/wire), Bank A (checks) | USD |
| e.g. *Acme Plant 2* | ap-plant2@… | VISUAL 9.0.8 (instance 2) | VMFG DB `VM908P` | VISUAL-UI/SQL #2 | A/P Invoice Entry | A/P Payment Entry | Bank B | USD |
| … | | | | | | | | |

**Cross-system supplier dedup key (XC-05):** normalized legal name + TIN/VAT + verified bank account (+ address as tiebreak). Suppliers active on multiple ERPs get one golden record with N ERP cross-references — this is what makes SO-14 propagation, VA-04 cross-ERP duplicate detection, and PY-02 supplier netting possible.

---

# Part 3 — Notes & Caveats

1. **Confidence:** SAP ECC t-codes/BAPIs/tables, SAP B1 Service Layer entities/tables, and NAV page/table/codeunit numbers are standard and widely documented (📄). AX 4.0 AIF coverage, BPCS/LX program & file names, VISUAL table names and API Toolkit coverage, PC/MRP storage format, and Global Shop tables/GAB options are version/installation-specific (⚠️) — confirm each against the installed system's data dictionary or vendor before adapter build. Nothing in Part 2 marked ⚠️ should be treated as build-ready without that confirmation.
2. **Licensing gates before any write adapter goes live** (verified findings in the companion report §4.4): SAP indirect access / Digital Access for every ECC-posted document; Microsoft multiplexing for AX/NAV; Infor, PC/MRP, and Global Shop EULA terms for automated sessions; UiPath unattended runtime sizing for the RPA-channel adapters.
3. **Payment-run suppression:** because the platform pays (PY), each ERP's native payment run must be disabled or fenced for platform-managed entities (SAP F110 variants, NAV *Suggest Vendor Payments*, AX payment proposals, VISUAL/GSS check runs) to prevent double payment — an operational cutover task per entity, not a platform function.
4. **Two VISUAL instances = two adapters** with separate credentials, selector repositories, and cross-reference maps, even though the surface list is identical.
5. **RPA-channel adapters** (BPCS/LX, VISUAL, AX, PC/MRP, GSS UI paths) inherit all stability requirements from the companion report §4: unattended session resolution pinning, dedicated ERP accounts per robot, queue-based REFramework design with idempotency checks, and state-based synchronization.
