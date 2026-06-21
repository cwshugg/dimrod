# Grocer — Grocery List Service

Grocer manages the household grocery list, which is stored as a Todoist project named **"Groceries"**.

## Purpose

* Provide HTTP endpoints for querying and modifying the grocery list
* Automatically sort grocery items into categories using an LLM
* Expand recipe references into their individual ingredients via the [Chef](chef.md) service
* Merge duplicate ingredient entries into a single item
* Expose natural-language actions for adding, removing, and listing groceries

Grocer replaces the old grocery-related task jobs that previously lived in the [Taskmaster](taskmaster.md) service. Those task jobs were removed; their logic was ported into Grocer's dedicated worker threads.

## Architecture

`GrocerService` keeps its main service thread lightweight — it simply sleeps in a loop. All real work is performed by three daemon worker threads and by the Oracle HTTP endpoints.

The grocery list lives in a Todoist project called **"Groceries"** (created automatically if it does not exist). Todoist *sections* within that project act as the grocery *categories*, and Todoist *tasks* act as the grocery *items*.

### Concurrency

Because the worker threads and the HTTP endpoints can all touch Todoist at the same time, Grocer guards every Todoist interaction with a shared **readers-writer lock** (`ReadWriteLock`, exposed as `service.todoist_lock`):

* Read operations (listing categories/items) acquire a **read** lock — multiple readers may proceed concurrently.
* Write operations (adding/removing/sorting/resolving/deduplicating) acquire a **write** lock — exclusive access.
* Writers are given priority over new readers to prevent writer starvation.

### Item Identifiers

Grocery items are referenced externally by a stable, opaque ID rather than by raw Todoist task IDs. The `derive_item_id()` helper computes a SHA-256 digest of `"grocery_item_<TASK_ID>"`, so internal Todoist IDs are never exposed to clients.

## Worker Threads

Grocer launches three daemon threads on startup. Each is a thin loop wrapper that repeatedly calls a `GrocerService` operation method (which acquires the readers-writer lock internally) and then sleeps for a configurable interval before the next iteration.

| Thread | Operation method | Refresh-rate config | Description |
|--------|------------------|---------------------|-------------|
| **Auto-Sorter** (`grocer-autosort`) | `sort_items()` | `autosort_refresh_rate` | Uses the LLM to sort unsorted items into the correct Todoist section (category) |
| **Recipe-Resolver** (`grocer-recipe-resolver`) | `resolve_recipes()` | `recipe_resolver_refresh_rate` | Finds recipe references and expands them into ingredient items via Chef |
| **Deduplicator** (`grocer-deduplicator`) | `deduplicate_items()` | `deduplicator_refresh_rate` | Merges items that share the same ingredient ID into a single item |

Each operation method returns a status string (`"... completed successfully."` or `"... failed: <reason>"`), which is also used by the on-demand endpoints to report success or failure.

### Auto-Sorter

`sort_items()` fetches the project's sections and tasks, determines which tasks are "dirty" (newly added or moved to a different section than last recorded), and asks the LLM to assign each dirty item to one of the available categories. The LLM is instructed to respond with one `item|CATEGORY` line per item, which Grocer parses to move tasks into the matching section.

A persistent **sort record** (`.grocer_sort_record.pkl`, managed by `GrocerySortRecord`) tracks which item was last placed in which section, so already-sorted items are not re-sorted on every iteration. Tasks that look like recipe references are skipped (and tagged with the `dimrod::autosort_ignore` magic string) so the recipe-resolver can handle them instead.

### Recipe-Resolver

`resolve_recipes()` scans the list for items whose title contains the `recipe` magic string, resolves each to a concrete recipe by calling the Chef service (`/recipes/resolve`, `/recipes/get_by_id`), then expands the recipe into one grocery item per ingredient. Ingredient quantities are scaled by the resolved serving multiplier, and the original recipe-reference task is deleted.

To prevent the same recipe reference from being expanded twice when two passes overlap (the periodic resolver thread plus an on-demand `/groceries resolve`/`process`), each eligible recipe task is **atomically claimed** under the write lock before the slow Chef work runs: its description is tagged with the `dimrod::recipe_resolution_underway` magic string. Because the "is it already claimed?" check and the marker write both happen while the write lock is held, a concurrent pass sees the marker and skips the task. The original task is deleted only after **all** of its ingredient items are added successfully; if any add fails, the original is left in place and un-claimed (the underway marker stripped) so it re-resolves next cycle without losing ingredients. Transient Chef failures likewise un-claim the task, so a recipe is never left permanently stuck "underway".

If Chef cannot find a matching recipe, the task is left in place, re-titled with a `❓` marker, and tagged with the `dimrod::recipe_resolution_failure` magic string so it is not retried (the underway marker is removed at the same time).

### Deduplicator

`deduplicate_items()` groups items by their embedded ingredient ID (see [Magic-String Protocol](#magic-string-protocol)). For each group of two or more items, it sums the parsed quantities, merges the descriptions (dropping duplicate lines), updates the first task with the merged title/description, and deletes the rest.

## Magic-String Protocol

The recipe-resolver and deduplicator coordinate through magic strings embedded in task titles and descriptions:

| Magic string | Location | Meaning |
|--------------|----------|---------|
| `recipe` | Title | Triggers the recipe-resolver to treat the item as a recipe reference |
| `dimrod::ingredient_id::<id>` | Description | Embeds the Chef ingredient ID so the deduplicator can group identical ingredients |
| `dimrod::expanded_recipe_ingredient` | Description | Marks an item that was already expanded from a recipe (so it is not re-resolved) |
| `dimrod::recipe_resolution_failure` | Description | Marks a recipe reference that Chef could not resolve (so it is not retried) |
| `dimrod::recipe_resolution_underway` | Description | Marks a recipe reference that is currently being resolved (claimed under the write lock) so a concurrent resolution pass skips it; removed when resolution reaches a terminal state or the task is un-claimed |
| `dimrod::autosort_ignore` | Description | Marks a recipe reference so the auto-sorter skips it |

### Ingredient Title Format

When the recipe-resolver expands a recipe, each ingredient with a non-unit quantity is titled using the `(Nx) <name>` format — for example `(2x) Ground Beef` or `(1.5x) Milk`. The deduplicator parses this format (via the `QUANTITY_RE` pattern) to recover the quantity and base name when summing duplicates; items without a prefix are treated as quantity `1`.

## Oracle Endpoints

All endpoints require an authenticated session and use `POST`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/categories` | List all categories (Todoist sections) in the Groceries project |
| `POST` | `/items` | List all grocery items, each with a derived `id`, `title`, `description`, and `section_id` |
| `POST` | `/items/add` | Add a new grocery item |
| `POST` | `/items/remove` | Remove a grocery item by its derived `id` |
| `POST` | `/items/sort` | Trigger an on-demand auto-sort (`sort_items`) |
| `POST` | `/items/resolve_recipes` | Trigger on-demand recipe resolution (`resolve_recipes`) |
| `POST` | `/items/deduplicate` | Trigger on-demand deduplication (`deduplicate_items`) |

### `/items/add` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `title` | Yes | The grocery item's name |
| `description` | No | Optional item description (default: empty) |
| `section_id` | No | Todoist section (category) to place the item in (default: none) |

Returns the new item's derived `id`, its Todoist `task_id`, and its `title`.

### `/items/remove` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | The derived item ID (as returned by `/items` or `/items/add`) |

Returns a success message, or `404` if no item matches the given ID.

The three operation endpoints (`/items/sort`, `/items/resolve_recipes`, `/items/deduplicate`) take no request body. They run the corresponding operation synchronously and return its status message, with a `500` status if the operation reports failure.

## NLA Endpoints

Grocer registers four [Natural Language Action](../data-types.md#nla-types) endpoints so the [Speaker](speaker.md) LLM can dispatch conversational grocery requests:

| NLA endpoint | Handler | Triggers |
|--------------|---------|----------|
| `list_categories` | `nla_list_categories` | "what grocery categories are there?", "list grocery sections" |
| `list_grocery_items` | `nla_list_grocery_items` | "what's on the grocery list?", "show me the groceries" |
| `add_grocery_item` | `nla_add_grocery_item` | "add milk to the grocery list", "I need eggs" |
| `remove_grocery_item` | `nla_remove_grocery_item` | "remove milk from the grocery list", "take bananas off the groceries" |

The add/remove handlers use a set of regex preamble patterns (`_extract_item_name`) to strip phrases like "add ... to the grocery list" or "I need ..." and recover the bare item name.

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `todoist` | `TodoistConfig` | Todoist API credentials (required) |
| `dialogue` | `DialogueConfig` | OpenAI settings for the auto-sort LLM (required) |
| `chef_oracle` | `OracleSessionConfig` | Connection to the Chef service (required) |
| `autosort_refresh_rate` | `int` | Auto-sorter loop interval, seconds (default: `120`) |
| `recipe_resolver_refresh_rate` | `int` | Recipe-resolver loop interval, seconds (default: `120`) |
| `deduplicator_refresh_rate` | `int` | Deduplicator loop interval, seconds (default: `120`) |

## Dependencies

* **Library modules:** `lib.todoist`, `lib.oracle`, `lib.dialogue`, `lib.nla`, `lib.service`, `lib.config`, `lib.cli`
* **Other services:** [Chef](chef.md) (recipe lookup and resolution), [Speaker](speaker.md) (NLA dispatch), [Telegram](telegram.md) (the `/groceries` command)
* **External APIs:** Todoist, OpenAI (for auto-sort categorization)

## Integrations

### Telegram `/groceries` command

The [Telegram](telegram.md) bot's `/groceries` command (aliases: `/grocery`, `/grocer`, `/groc`, `/g`) is the primary user interface to Grocer. It authenticates an `OracleSession` with Grocer and calls its endpoints:

| Subcommand | Grocer endpoint(s) |
|------------|--------------------|
| `/groceries` (or `items`, `list`) | `/items`, `/categories` — list items grouped by category |
| `/groceries categories` (or `cats`) | `/categories` |
| `/groceries sort` | `/items/sort` |
| `/groceries dedup` (or `deduplicate`) | `/items/deduplicate` |
| `/groceries resolve` (or `recipes`) | `/items/resolve_recipes` |
| `/groceries process` (or `all`) | `/items/resolve_recipes`, then `/items/deduplicate`, then `/items/sort` |

### Chef

The recipe-resolver thread depends on the [Chef](chef.md) service to turn recipe references into ingredient lists. It logs into Chef via an `OracleSession` and calls Chef's `/recipes/list_all`, `/recipes/resolve`, and `/recipes/get_by_id` endpoints.

## Notable Details

* The worker threads are intentionally thin: all business logic and locking live in `GrocerService.sort_items`, `resolve_recipes`, and `deduplicate_items`, while `threads.py` only provides the loop-and-sleep wrappers.
* Todoist API calls are wrapped with rate-limit retry handling (`_get_project_with_retry`) — on an HTTP `429`, Grocer sleeps and retries up to a fixed number of attempts.
* The auto-sort record (`.grocer_sort_record.pkl`) is pruned each cycle to drop entries for items that no longer exist on the list.
