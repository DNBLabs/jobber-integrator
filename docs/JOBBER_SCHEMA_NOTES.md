# Jobber GraphQL schema notes

This file documents schema assumptions used by the Price Sync app. Verify in [GraphiQL](https://developer.getjobber.com/docs/getting_started/) (Manage Apps → Test in GraphiQL → Documentation) for your API version.

## Product / Service

- **Query:** `productOrServices(first, after)` returns a connection with `nodes` (or `edges { node }`), `pageInfo { hasNextPage, endCursor }`.
- **Node fields used:** `id`, `name`, `internalUnitCost`. With Enhancement 1: `code` (SKU) when available.
- **Mutation:** `productsAndServicesEdit(productOrServiceId: EncodedId!, input: { ... })` returns `{ productOrService { id }, userErrors [] }`.

### Cost

- **internalUnitCost** (Float): Unit cost. We set this from the CSV `Trade_Cost`.

### Unit price (selling price) – Enhancement 5

- **Verified:** The `ProductOrService` type in Jobber’s schema uses **`defaultUnitCost`** (Float!) for “A product or service has a default price” (selling price). The same field name is used in the `productsAndServicesEdit` mutation input.
- We send both `internalUnitCost` and `defaultUnitCost` in one call when markup % is set; formula: `defaultUnitCost = cost * (1 + markup_percent/100)`, rounded to 2 decimals.
- If a future API version renames this field, update `app/sync.py`: `MUTATION_UPDATE_COST_AND_PRICE` and the variables in `_update_cost_and_price`.

## Version

- **X-JOBBER-GRAPHQL-VERSION** used: `2026-02-17` (see `app/sync.py`). Newer versions may add or rename fields; check the schema before upgrading.
