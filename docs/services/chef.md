# Chef — Recipe Service

Chef is a recipe registry that stores, retrieves, and resolves recipe queries using an LLM.

## Purpose

* Load and cache recipes from JSON files on disk
* Serve recipe data via HTTP endpoints
* Resolve natural-language recipe queries to specific recipes using the LLM

## Architecture

`ChefService` runs a tick-based loop that periodically refreshes its in-memory recipe cache from JSON files in the configured `recipe_dir`. It validates recipes on load and checks for duplicate IDs.

Recipe resolution uses the `DialogueInterface` to match a free-text query (e.g., "something with chicken") to the best matching recipe from the registry, with configurable retry attempts.

## Oracle Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/recipes/get_by_id` | Get a recipe by its unique ID |
| `POST` | `/recipes/list_all` | List all recipe names and IDs (summary only) |
| `POST` | `/recipes/get_all` | Get all recipes with full data |
| `POST` | `/recipes/resolve` | Resolve a text query to a specific recipe |

### `/recipes/resolve` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `text` | Yes | Natural-language recipe query |

Returns the matching recipe's `id` and `quantity` (number of servings).

## NLA Endpoints

None.

## Recipe Data Model

**`Recipe`:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique recipe identifier |
| `title` | `str` | Recipe name |
| `description` | `str` | Recipe description |
| `servings` | `int` | Number of servings |
| `ingredients` | `list[Ingredient]` | Required ingredients |
| `steps` | `list[RecipeStep]` | Preparation steps |
| `links` | `list` | Related links |
| `icon` | `str` | Display icon |

**`Ingredient`:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique ingredient identifier |
| `title` | `str` | Ingredient name |
| `description` | `str` | Additional details |
| `quantity` | `float` | Required amount (default: `1.0`) |
| `replenish` | `IngredientReplenishType` | Replenishment frequency: `ALWAYS` (0), `SOMETIMES` (1), or `RARELY` (2). Default: `ALWAYS` |
| `is_optional` | `bool` | Whether the ingredient is optional |

**`RecipeStep`:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique step identifier (required) |
| `title` | `str` | Step name |
| `description` | `str` | Step details |

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `recipe_dir` | `str` | Directory containing recipe JSON files |
| `dialogue` | `DialogueConfig` | OpenAI settings for recipe resolution |
| `recipe_refresh_rate` | `int` | Cache refresh interval (seconds) |
| `resolve_recipe_dialogue_retries` | `int` | LLM retries for query resolution |
| `resolve_recipe_default_servings_needed` | `int` | Default servings when not specified |

## Dependencies

* **Library modules:** `lib.dialogue`, `lib.oracle`, `lib.service`
* **External APIs:** OpenAI (for recipe resolution)
* **Other services:** None

## Notable Details

* Recipes are stored as individual JSON files, one per recipe
* The Telegram bot's `/recipes` command calls Chef's endpoints
* Recipe resolution uses an LLM prompt/parse loop — the LLM is given the list of available recipes and asked to pick the best match
