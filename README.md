# CC Medical Inventory

Internal web app for tracking CC Medical's ultrasound equipment: stock, cores, customer repairs, customers and the product catalog. Built with Python (Flask) and one SQLite database file.

**Status: sandbox.** This repo (`SGccmed/ccmed-inv-sandbox`, forked from `bvcarli2/ccmedical-inventory`) runs on Render's free plan with fake demo data. Real data waits until we move to a paid plan (see the last section).

## What's in the app

The **home page** (click **CC Medical** at the top left of any page) has a big search bar, quick links, counts of what's waiting on you, your **Favorites** (☆ Favorite at the top right of any page) and the records you opened recently.

**The sidebar** is the same on every page, in the same order, with the current page highlighted. Each group has an icon:

- **Home** (house)
- **Inventory** (box): **+ New Inventory** (receiving; Admins and Shipping), then Inventory, which opens Active stock, with a drop-down to switch to Sold or All. **Loaners**, **Rental**, **Pending** and **Needs Review** show how many items each holds; Pending turns yellow and Needs Review red when they have any.
- **Repairs** (wrench): Customer Repairs (RP) and Core Repairs (CR), each with its number of open tickets or cores.
- **Reference** (file cabinet): Customers, Product Database.
- **Shipping** (truck): the **Shipping queue**: repairs ready to ship (with a count), repairs returning from a partner (Inbound), items out on loan or rental, and placeholders for stock orders and expected arrivals until the Shipping rework.
- **Admin** (gear, Admins only): Users, Admin Mode.
- **Account** (person): who you're signed in as, Change password, View as, Sign out.

**Collapsing it (since v1.16):** on a wide screen the « button at the top folds the sidebar into a thin rail of icons; click or hover an icon to open its links beside it. Your browser remembers the choice. On narrower windows and phones the sidebar is a bar of icons across the top; tap one to open its links under the bar. A red dot on the Inventory icon means items need review (yellow: pending).

**Themes (since v1.16):** Light, Dark (blue/black) and OLED (true black, dark blue) are picked in the **Home** page header. The choice is saved to your account, so it follows you to any computer. `theme.py` applies it to every page.

Links a role can't use aren't shown (technicians don't see Loaners or Rental). The total recorded cost of stock (active, and out on loan or rental) is on the **home page for Admins and SuperAdmins** only.

| Area | What it tracks | ID format |
|---|---|---|
| Stock (CC) | Units we own: shelf location, cost, status | `CC-MMDDYY-###` |
| Cores (CR) | Broken units we're fixing to sell, worked through ticket updates; converts to stock when ready | `CR-MMDDYY-###` |
| Customer repairs (RP) | Repair tickets for customer units, run like a helpdesk: 14 stages, team queues, work log | `RP-MMDDYY-###` + RMA # |
| Customers | Customer accounts, contacts, activity log, repair history | `CL-00001` |
| Product Database | Master list of systems, probes and parts | — |
| Users | Staff logins and roles | username |

Every page needs a staff login. Serial numbers are **not** unique keys: a matching serial anywhere shows an alert but never blocks saving, because different manufacturers can share serials.

## Files

| File | What it does |
|---|---|
| `app.py` | Main app: stock list (`/inventory`), receiving, backup/restore, spreadsheet import |
| `home.py`, `home.html` | Home page: search, work tiles, favorites and recent pages |
| `choices.py` | The pick lists: inventory locations and manufacturers (both locked). Edit the lists here to add or rename one |
| `theme.py` | Light / Dark / OLED: the palettes and the colour swap applied to every page for the signed-in person's theme |
| `shipping.py` | The Shipping queue page (a first version until the Shipping rework) |
| `history.py` | The stock item's Product history (newest first) and Send to repair |
| `costs.py` | Cost adjustments (Admins): corrects a cost or quoted price with a required reason, keeps the history |
| `cores.py` | Core repairs: ticket updates, statuses, costs, converting to stock |
| `formatting.py` | Pacific-time dates ("Oct 30, 2026"), safe display of rich text notes, work hours, sortable column headers |
| `page_tools.html` | Added to every page: rich text editor, red warning on negative costs, Start/End time totals, the ☆ Favorite button, and the drop-down lists (customers, locations, manufacturers, models) |
| `trix.js`, `trix.css` | Trix rich text editor (open source, MIT licence, by 37signals), served by the app itself |
| `repairs.py` | Customer repair tickets: the stages, who can move each one, queues, work log |
| `search.py` | Search across customers, tickets, stock and cores |
| `sidebar.html` | The one sidebar used on every page: icon groups, counts, highlighting, collapse to icons, top bar on phones |
| `search_box.html` | The search box shown at the top of every sidebar |
| `salesforce.py` | Linking customers to their Salesforce Account IDs (CSV import) |
| `auth.py` | Logins, roles and permissions (`PERMISSIONS`), the Users page, loading staff accounts |
| `clients.py` | Customers pages (their HTML is inside this file) |
| `products.html` | Product Database screens: product list with open requests, a product's stock and details, the admin edit page |
| `product_database.py` | Product Database pages (their HTML is inside this file) |
| `dashboard.html` | Stock list and single-item page |
| `inventory_entry.html` | + New Inventory receiving form |
| `repairs.html` | Repair queue, new ticket and ticket pages |
| `cores.html` | Core list and core page (ticket updates) |
| `seed_demo.py` | Builds `demo_inventory.db` with fake test data |
| `wsgi.py` | Starting point Render uses to launch the app |
| `requirements.txt` | Python packages: Flask, openpyxl, gunicorn, tzdata (time zones), nh3 (cleans rich text) |
| `render.yaml` | **Not used yet.** Settings for the future paid Render plan |
| `.gitignore` | Keeps the database, secrets and spreadsheets out of GitHub |

## Staff roles

Each account has one or more roles. The full rules are in `PERMISSIONS` in `auth.py` and are checked on the server for every action.

- **SuperAdmin**: everything, plus Admin accounts, database backup and restore.
- **Admin**: all inventory, customers, catalog and settings, linking customers to Salesforce. Manages Sales, Shipping and Technician accounts.
- **Sales**: customers, repair tickets, repair pricing and approvals, inventory lookups and purchase costs.
- **Shipping**: receiving, shelf moves, repair tickets, shipping repairs back (Ready to Ship to Shipped) and closing them once shipped.
- **Technician**: repair evaluation and repair work, ticket updates on repairs and cores (can enter parts costs but not see amounts), shelf moves, opening their own repair tickets (assigned to themselves). Can read the Product Database, Active and All lists, but has no Sold, Loaners or Rental lists; a single record still opens from search results or a serial alert. On a repair ticket a technician edits eval findings, location, assignment and updates; serial, model, part number, manufacturer, customer and RMA corrections are for Shipping, Sales or Admin. No costs or prices.

**View as (SuperAdmin):** the *View as…* menu at the bottom of the sidebar shows the app exactly as an Admin, Sales, Shipping or Technician account would see it. A yellow box shows while it's on; *Back to SuperAdmin* switches back.

There is always at least one active SuperAdmin, and nobody can change their own access.

## Repair tickets

Each customer repair is a ticket with one owner at each stage, like an IT helpdesk. The stage tells you whose turn it is, and each team has its own queue.

| Stage | Whose turn | Next |
|---|---|---|
| Received | Repair team | In Evaluation, or At Partner |
| In Evaluation | Repair team | Eval Complete, or Unrepairable |
| Eval Complete | Sales (adds pricing) | Awaiting Approval (warranty tickets go straight to Approved) |
| Awaiting Approval | Sales (on hold) | Approved, or Declined |
| Approved | Repair team | In Repair, or At Partner |
| In Repair | Repair team | Repair Complete, Waiting on Parts, or Unrepairable |
| Waiting on Parts | Repair team (on hold) | In Repair |
| Repair Complete | Sales | Ready to Ship |
| Ready to Ship | Shipping | Shipped, or In Repair (send to a tech for inspection) |
| Shipped | Shipping or Admin (Admin adds the invoice #) | Closed |
| Closed | — | Admin can reopen |
| At Partner | Sales (on hold) | Eval Complete, Returning from Partner, Shipped, or Ready to Ship |
| Returning from Partner | Shipping | Ready to Ship, or In Repair (inspection) |
| Declined | Sales | Ready to Ship (returned free) |
| Unrepairable | Sales | Closed (exchanged), or Ready to Ship |

- **One Ticket update box (since v1.17, like the core page):** day worked on, Start and End time (the Total in hours fills in by itself, e.g. 1.75 hrs), the update in rich text, parts cost, additional cost, an optional link (a OneDrive report, photos), and a **Status update** drop-down that moves the ticket on. Everything is optional, but something has to be filled in. The ticket shows the total time logged.
- **Moving a ticket:** the Status update drop-down lists only the stages your role can move it to (it's the team whose turn it is); Admins also get "Admin: any stage", including reopening a closed ticket. Every move, update and edit goes in the ticket's work log with who and when; an update that also changes the stage is one entry.
- **Shipping a repair:** choosing **Shipped** asks for the carrier (UPS, FedEx, USPS, DHL, Freight, Courier, Customer pickup, Hand delivered, Other) and the tracking number (not needed for pickup or hand delivery). Both are saved on the ticket with a link to the carrier's tracking page, and listed under **Recently shipped** on the Shipping queue for 14 days. Shipping, Sales and Admins can correct them under Ticket details.
- **Partner (outsourced) repairs (since v1.20):** before approval, At Partner → **Eval Complete** brings the partner's evaluation back to Sales to quote. After approval the drop-down offers the ways back instead: **Returning from Partner** (coming back to the office; asks for the partner's carrier and tracking number) or **Shipped** (the partner ships straight to the customer). Sales or Shipping can make those moves. A second evaluation is still possible under "Additional eval / re-quote". Returning tickets are listed on the Shipping queue's **Inbound** tab; when one arrives Shipping moves it to Ready to Ship, or to In Repair if a tech should inspect it first (Ready to Ship can also go to In Repair for an inspection).
- **Tracking at a glance:** a ticket with tracking shows it under the guide text, with links, and an **Edit tracking** link that jumps to the fields in Ticket details (corrections are saved with Save details and logged).
- **Closing:** choosing **Closed** shows the resolution picker ("Work it out for me" by default). Since v1.19 **Shipping can close a Shipped ticket** as well as Admins, since they handle the last step; Recently shipped on the Shipping queue marks tickets that aren't closed yet. Admins can still add the invoice number after a ticket is closed.
- **The queue** can be sorted by any column (click the header; click again to reverse). It opens longest-in-stage first and has a Received date column.
- **On hold** stages don't turn yellow or red.
- **RMA numbers** are created automatically (RMA-10001, ...) if left blank; the field can also hold a partner's RMA.
- **Closed tickets** record a resolution: Repaired, Warranty, Declined, Exchanged, or Returned unrepaired.
- **My tickets** shows repairs assigned to you plus repairs for customers you own as sales rep. The Sales rep and Assigned tech filters show anyone else's.
- Databases from before v1.05 are upgraded automatically: Open Repairs becomes Received, Evaled becomes Eval Complete, QC becomes In Repair, Completed becomes Closed.

## Core repairs

A core is worked like a ticket. Technicians post **ticket updates**: the day worked on (today by default), optional Start and End times (Total in hours is worked out for you), the update itself in a rich text box (bold, numbered and bulleted lists), parts cost, additional cost, and a status change. Updates are signed with the technician's name automatically; Admins can post on a technician's behalf.

| Status | Meaning |
|---|---|
| Needs Repair | Waiting for a technician |
| In Repair | Being worked on |
| Waiting on Parts | On hold until parts arrive (doesn't age) |
| On Hold | Paused for another reason, including cores we can't fix right now (doesn't age) |
| Ready for Stock | Fixed and tested; the update that sets it is the QC sign-off |
| Converted to Stock | An Admin converted it into a CC stock item carrying the full cost |
| Returned to Stock | A stock item that was sent to repair is back on a shelf (same item and ID), with the repair costs added to its cost |

**Sending stock to repair (since v1.15):** on a stock item's page, Admin, Sales and Shipping can **Send to repair** with a short reason. That opens a core repair ticket linked to the item and moves the item to the Repair location (its status doesn't change; the page shows "In repair" with a link). Technicians work it like any core. At Ready for Stock an Admin clicks **Return to stock** and picks the shelf: the same item goes back with the repair costs added to its cost. One open repair per item at a time. Customer units come in as customer repair tickets as before.

## Product history

Every stock item's page has a **Product history**, newest first, under a "Now" line (status, location, and any open repair):
received (who, when, shelf; or the core it came from), every core ticket update, conversion, shelf moves (who and why), sends to repair and returns, cost changes (only for roles that see costs), old repair records from before v1.15, and customer repair tickets with the same serial **and** the same product, which are most likely this unit coming back from a customer. Those tickets also link back to the stock item. A record with only the same serial (a different product) stays a yellow warning, because serials repeat across manufacturers. The core a stock item was converted from is shown as **Came from**, not as a duplicate serial.

The core list can be sorted by any column and shows hours logged. Technicians can enter costs but never see amounts or totals; the cost summary is for Sales, Admin and SuperAdmin. Cores in the old QC status become In Repair automatically.

## Drop-down lists, locations and manufacturers

Every customer, location, manufacturer and model field is a type-to-search drop-down drawn by the app (since v1.14), so it looks and works the same in every browser and on phones. It opens when you click or type, ignores capitals and spaces, shows 8 rows and then scrolls. Arrow keys move, Enter picks (it doesn't save the form), Esc closes. The browser's own autofill is switched off on these fields. The location and manufacturer lists live in `choices.py`.

- **Customers** are looked up live, so a customer added in another tab shows up without refreshing. Each row shows the name, the sales rep and the CL- number. Leaving the box with exactly one match fills it in (it flashes green). With no match the box turns red ("No customer by that name") and the save button greys out; with several it asks you to pick one. People who can add customers see **+ Add new customer** at the bottom of the list (opens in a new tab). The server checks the customer again when you save.
- **Model / Product** suggests products from the Product Database (name, manufacturer, type), narrowed to the manufacturer you picked. Picking one fills in a blank manufacturer and ties the stock item straight to that product. You can still type a model that isn't in the catalog: on + New Inventory a yellow note warns it won't appear on a product page, and a stock (CC) item saved that way goes to **Needs review** (Loan/Rental items keep their status but get the same review note). Once the product is added to the Product Database, the item links to it and leaves Needs review by itself.
- **Nothing typed is lost:** if + New Inventory, a new repair ticket or Request this item finds a problem when you save, the form comes back with everything you entered and a message saying what to fix (on + New Inventory the item is outlined in red).

- **Locations** must come from the list: A1–A4, B1–B4, C1–C4, T1, UG, 2D, Loan, Rental, Repair, ACER, LEN, INT, PBO. Anything else turns the field red and greys out the save button, and the server refuses it too. Typing in lower case is fine (`a1` is saved as `A1`).
- **Loan and Rental** also set a stock item's status (On loan / Rental); moving it to any other location makes it Active again. They're only offered for stock (CC) items, and technicians can't move items to or from them. Cores and repair tickets use the other locations.
- **Repair** is a single location for now; it can be split into racks later by adding them to the list.
- **Manufacturers** are a fixed list too (since v1.11): GE, Philips, Canon/Toshiba, SonoSite, BK, Siemens/Acuson, Samsung. Common variants ("Cannon", "Toshiba", "Acuson", "Fujifilm SonoSite") are saved with the list's spelling; any other make is refused, so a wrong manufacturer can't end up on a record. To receive a make that isn't listed, add it to `MANUFACTURERS` in `choices.py` first. The field can still be left blank.
- Records saved before v1.10/v1.11 keep their old location or manufacturer until someone changes that field.

## Notes and costs

- **Rich text:** every note and description box (ticket updates, repair intake notes, eval findings, customer notes and activity, product descriptions and requests) is a rich text editor. Ctrl+B / Ctrl+I work, and numbered or bulleted lists continue when you press Enter. Notes are stored as HTML and cleaned of anything unsafe every time they're shown. Older plain-text notes still display normally.
- **Adjusting a cost (Admin, SuperAdmin):** a stock item's cost, a core's purchase cost, the parts or additional cost on one core update, and a repair's quoted price can each be corrected on the record's page. A reason is required; the old and new amounts, who and when are kept with the cost. On a stock item it's one **Cost** box: the current cost, every change, and (for Admins) the Adjust form. Sales sees the box without the form; technicians and Shipping don't see it. A converted core is adjusted through its stock item.
- **Sorting:** the stock list, repair queue and core list sort by clicking a column header.
- **Unsaved changes:** ticket updates (repairs and cores), + New Inventory and the new repair ticket form ask before you leave the page with something typed in them.
- **Search shortcut:** press **/** on any page to jump to the search box.
- **Costs:** a negative or non-numeric cost turns the field red with a warning and greys out the form's submit button until it's fixed. The server also refuses negative costs.

## Dates

Every date is shown in Pacific time as **Oct 30, 2026**; work logs and updates add the time (**Oct 30, 2026 · 2:15 PM**). Dates are stored in UTC.

## Product Database and stock requests

- **Product list**: every model with how many are **Available**, **On loan / rental** and **Sold**, filtered by search, manufacturer, type and "in stock only". The review status column is for Admins.
- **A product's page** opens on **Stock**: the units we have, in three groups (Available, On loan / rental, Sold), each narrowed by location or serial and sorted by location, serial or newest. **Details** holds the manufacturer description, specs, OEM part numbers, compatibility and history. Admins use **Edit product** for all changes. Technicians only see what's available.
- **Linking units to products**: each stock item is tied to its catalog product when its model matches the product's name (e.g. "GE C1-6-D REFURB" → GE C1-6-D). This happens on start-up, when items are received and when the catalog changes. The + New Inventory model box suggests catalog models, and each item's page links its product.
- **Request this item** (on a product's page): quantity, optional customer, optional note. Requests are listed at the top of the Product Database page until someone ticks **Done**: an Admin, the person who asked, or the customer's sales rep. Recently closed requests can be shown again. (Email notifications and closing automatically when a unit is received are planned.)
- **Suggest a new product** is for adding models or OEM part numbers to the catalog; Admins approve these in Admin Mode.

## Search

The box at the top of every sidebar searches customers (name, CL- number, Salesforce ID, phone, email), repair tickets (ticket, RMA, serial, customer, model), stock and cores. Serials match with or without spaces.

- **Suggestions** appear as you type; use the arrow keys and Enter, or click one.
- **Enter** with nothing highlighted runs the full search. If only one record matches, or one matches exactly (a full ticket number, serial or CL- number), it opens that record directly with a note saying so.
- **Several matches** (for example "San Diego") open a results page grouped into customers, repair tickets, stock and cores. "See all results" at the bottom of the suggestions always shows that page.
- Technicians don't see customers in search, matching their other permissions.

## Customers and Salesforce

Salesforce stays the system for customers, contacts and tasks; this app owns inventory and repairs. Each customer keeps its own CL- number and also stores its **Salesforce Account ID** (15 or 18 letters and digits, starting with 001). The ID must be unique per customer.

- **One at a time:** Customers → open a customer → Edit → *Salesforce account ID*.
- **In bulk (Admins):** Customers → **Link to Salesforce** (also in Product Database → Admin Mode). Upload a Salesforce report exported as CSV with **Account Name** and **Account ID** columns. The preview shows which customers will be linked, which are already linked, IDs that would change, names with no match, and bad rows. Nothing is saved until you click *Link selected customers*. Each link is recorded in the customer's history.
- **Open in Salesforce:** set `CCM_SALESFORCE_URL` (for example `https://yourcompany.lightning.force.com`) and each linked customer page gets an *Open in Salesforce* link.
- Keep real customer lists off the free sandbox. The demo data has fake IDs (`001DM…`), and `salesforce_demo_export.csv` is a fake export for testing the import.

## How the live site works (Render free plan)

GitHub is the master copy of the code. Render rebuilds the site automatically every time a change is committed to `main`.

### Updating the site

1. On GitHub, open the repo and choose **Add file → Upload files**.
2. Drag in the changed files (they replace the old ones) and commit to `main`.
3. In Render, watch the deploy log until it shows gunicorn "Listening" (a few minutes).

### Render settings

The service was created with **New → Web Service** (not Blueprint, because `render.yaml` asks for a paid plan).

| Setting | Value |
|---|---|
| Instance type | Free |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn wsgi:application --workers 1 --threads 4 --timeout 120` |
| Health check path | `/healthz` |
| Environment variables | `PYTHON_VERSION` = `3.12.7`, `CCM_SECRET` = a long random string |

Leave `CCM_DB_PATH` unset on the free plan.

### Free plan limits: data gets wiped

The free plan has no permanent disk. After about 15 idle minutes the site sleeps, and the database is erased on wake-up and on every deploy.

- **Demo data** must be re-uploaded: sign in as SuperAdmin, go to `/admin/restore` and upload `demo_inventory.db`.
- **Favorites, recent pages and cost adjustments** are in the database too, so they reset with it.

Never enter real customer or inventory data while on the free plan.

## Demo data

```
python seed_demo.py
```

This builds `demo_inventory.db`: 67 stock items, 8 cores, 21 repair tickets (every stage and path), 11 customers and 28 products, all fake. It has no user accounts, so restoring it keeps the current logins.

## Run it on your own computer (Windows)

1. Install Python 3.10 or newer from python.org (tick "Add Python to PATH").
2. Open Command Prompt in this folder and install the requirements:
   ```
   pip install -r requirements.txt
   ```
3. Create your first account (a SuperAdmin; it asks for a password of 10+ characters):
   ```
   python app.py create-admin yourname
   ```
4. Start the app, then open http://127.0.0.1:5000:
   ```
   python app.py
   ```

To import an existing inventory spreadsheet (sheet named "Location"):

```
python app.py import "C:\path\to\inventory.xlsx"
```

## Backups

A SuperAdmin can download the whole database from **Product Database → Admin Mode → Download database backup**, and replace it at `/admin/restore`. The restore page downloads a safety copy of the current data first; that's ticked by default. For demo data you can untick it: a second "Skip the safety copy?" step appears, and its **Yes, skip it** button unlocks after 5 seconds. Without the safety copy you land on Home with a summary of what was restored (for example "69 stock items, 22 repair tickets, 8 cores").

## Optional settings (environment variables)

| Variable | What it does |
|---|---|
| `CCM_SECRET` | Key that signs login sessions. Set it on Render so logins survive restarts |
| `CCM_DB_PATH` | Where the database file lives (default: `inventory.db` next to `app.py`) |
| `CCM_INITIAL_ADMIN_USER` / `CCM_INITIAL_ADMIN_PASSWORD` | Creates a first SuperAdmin when the site has no users |
| `CCM_SALESFORCE_URL` | Your Salesforce address, e.g. `https://yourcompany.lightning.force.com`; adds *Open in Salesforce* links on customer pages |
| `CCM_SECURE_COOKIES` | `1` (default) sends login cookies only over HTTPS. `python app.py` turns this off for local use |

## What stays off GitHub

The repo is public, so `.gitignore` keeps out the database (`*.db`), the secret key, the staff accounts file, and all spreadsheets (`.xlsx`, `.xls`, `.csv`).

## Later: moving to a paid plan

Before real data goes in, the site moves to a paid Render plan with a permanent disk. `render.yaml` already describes that setup (Starter plan, 1 GB disk at `/var/data`, database at `/var/data/inventory.db`). At that point:

1. Create the service from **New → Blueprint** using `render.yaml`, or add a disk to the existing service and set `CCM_DB_PATH=/var/data/inventory.db`.
2. Restore the real data at `/admin/restore`.
3. Optionally add a custom domain through Cloudflare: a CNAME record pointing to the `onrender.com` address, set to DNS only until Render verifies it.
