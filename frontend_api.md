# Pricer API Reference (Frontend Integration)

Base URL: `/api/`

Authentication: Most endpoints are public (no auth required). Admin endpoints require `IsAdminUser`. Anomaly endpoints require `TokenAuthentication` or `SessionAuthentication`.

Guest identity is tracked via a `guest_uuid` cookie, initialized by `/api/session/init/`.

---

## Session & Preferences

### Initialize Session
`GET /api/session/init/`

Returns (and sets cookie for) a guest UUID.

```json
{ "guest_uuid": "uuid-string" }
```

### Store Preferences
`GET /api/store-preferences/`
`PATCH /api/store-preferences/`

Get or update preferred store IDs for the current guest.

**PATCH body:**
```json
{ "store_ids": [1, 2, 3] }
```

**Response:**
```json
{ "store_ids": [1, 2, 3] }
```

---

## Reference Data

### List Cities
`GET /api/cities/`

```json
{
  "cities": [
    { "id": 1, "name": "Almaty", "slug": "almaty" }
  ]
}
```

### List Chains
`GET /api/chains/`

```json
{
  "chains": [
    { "id": 1, "name": "Magnum", "slug": "magnum", "source": "mgo", "logo": "https://..." }
  ]
}
```

### Category Tree
`GET /api/categories/`

```json
{
  "categories": [
    {
      "id": 1,
      "name": "Молочные",
      "emoji": "🥛",
      "level": 1,
      "priority": 0,
      "children": [
        { "id": 2, "name": "Молоко", "emoji": null, "level": 2, "priority": 0 }
      ]
    }
  ]
}
```

---

## Products

### List Products
`GET /api/products/`

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `city_id` | int | 1 | Filter by city |
| `page` | int | 1 | Pagination page |
| `search` | string | — | Full-text search on title, description, brand |
| `brand` | string | — | Exact brand match |
| `brand__icontains` | string | — | Partial brand match |
| `measure_unit` | string | — | Exact measure unit |
| `canonical_category_id` | int | — | Filter by canonical category |
| `categories__contains` | string | — | Filter by category array |
| `ordering` | string | — | `created_at`, `updated_at`, `title`, `min_price`, `max_price` |

**Response:**
```json
{
  "count": 100,
  "next": "https://.../api/products/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "uuid": "uuid-string",
      "title": "Молоко Lactel 3.2% 1л",
      "brand": "Lactel",
      "brand_canonical": "Lactel",
      "producing_country": "Kazakhstan",
      "image_url": "https://...",
      "measure_unit": "l",
      "measure_unit_kind": "volume",
      "measure_unit_qty": 1.0,
      "pack_count": 1,
      "categories": ["dairy", "milk"],
      "canonical_categories": ["Молочные", "Молоко"],
      "is_active": true,
      "linked_stores_count": 3,
      "min_price": 690.0,
      "max_price": 890.0,
      "anomalies": [],
      "title_manually_edited_at": null,
      "title_unified_at": "2024-06-01T10:00:00Z",
      "created_at": "2024-01-01T10:00:00Z",
      "updated_at": "2024-06-15T10:00:00Z",
      "stores": [
        {
          "store_id": 1,
          "store_name": "Magnum Almaty",
          "store_source": "mgo",
          "chain_id": 1,
          "chain_name": "Magnum",
          "chain_logo": "https://...",
          "price": 690.0,
          "previous_price": 720.0,
          "currency": "KZT",
          "in_stock": true,
          "url": "https://...",
          "ext_product_id": 123,
          "ext_product_title": "Молоко Лактель 3,2% 1л",
          "ext_product_brand_canonical": "Lactel",
          "ext_product_image": "https://...",
          "ext_product_measure_unit": "l",
          "ext_product_measure_unit_kind": "volume",
          "ext_product_measure_unit_qty": 1.0,
          "ext_product_pack_count": 1,
          "similarity_coef": 0.95,
          "ai_coef": 0.92,
          "duplicate_of_id": null
        }
      ]
    }
  ]
}
```

### Get Product Detail
`GET /api/products/{uuid}/`

**Query params:** `city_id` (int, default 1)

Returns the same fields as list plus:
- `description` — full product description
- `barcodes` — array of barcode strings
- `additional_images` — array of image URLs
- `product_links` — detailed link data with ext_product info
- `price_range` — aggregated price comparison

**`price_range` shape:**
```json
{
  "min": 690.0,
  "max": 890.0,
  "avg": 760.0,
  "savings": 200.0,
  "savings_percent": 22.47,
  "stores": [
    {
      "store_name": "Magnum Almaty",
      "store_source": "mgo",
      "chain_id": 1,
      "chain_name": "Magnum",
      "chain_logo": "https://...",
      "price": 690.0,
      "previous_price": 720.0,
      "discount_amount": 30.0,
      "currency": "KZT",
      "price_per_unit": 690.0,
      "in_stock": true,
      "url": "https://...",
      "ext_product_id": 123,
      "ext_product_title": "Молоко Лактель 3,2% 1л",
      "ext_product_brand_canonical": "Lactel",
      "ext_product_image": "https://...",
      "ext_product_url": "https://...",
      "ext_product_measure_unit": "l",
      "ext_product_measure_unit_kind": "volume",
      "ext_product_measure_unit_qty": 1.0,
      "ext_product_pack_count": 1,
      "similarity_coef": 0.95,
      "ai_coef": 0.92
    }
  ]
}
```

### Price History
`GET /api/products/{uuid}/price-history/`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `days` | int | 30 | Days to look back (max 365) |
| `city_id` | int | — | Filter by city |

```json
{
  "product_uuid": "uuid-string",
  "product_title": "Молоко Lactel 3.2% 1л",
  "days": 30,
  "stores": [
    {
      "store_id": 1,
      "store_name": "Magnum Almaty",
      "chain_source": "mgo",
      "ext_product_id": 123,
      "ext_product_title": "Молоко Лактель 3,2% 1л",
      "prices": [
        { "date": "2024-06-01", "datetime": "2024-06-01T10:00:00Z", "price": 720.0, "in_stock": true },
        { "date": "2024-06-10", "datetime": "2024-06-10T08:00:00Z", "price": 690.0, "in_stock": true }
      ]
    }
  ]
}
```

---

## Search

### Algolia Search
`GET /api/search/`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | string | **required** | Search query |
| `hitsPerPage` | int | 20 | Results per page (max 100) |
| `page` | int | 0 | Page number (0-indexed) |
| `city_id` | int | 1 | Filter by city |
| `chain_ids` | string | — | Comma-separated chain IDs, e.g. `"1,2,3"` |
| `index` | string | `prod_canonical_products` | Algolia index |
| `disable_filter` | string | `"false"` | `"true"` to disable city filtering |

```json
{
  "hits": [ /* ProductListSerializer objects */ ],
  "nbHits": 100,
  "page": 0,
  "nbPages": 5,
  "hitsPerPage": 20,
  "query": "молоко",
  "processingTimeMS": 42
}
```

### Algolia Config
`GET /api/algolia-config/`

Returns public Algolia credentials for client-side search.

```json
{
  "app_id": "ALGOLIA_APP_ID",
  "search_api_key": "public_search_key",
  "index_name": "prod_canonical_products"
}
```

---

## Deals & Discounts

### Best Deals
`GET /api/best-deals/`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `city_id` | int | 1 | Filter by city |
| `limit` | int | 20 | Max results (max 50) |
| `min_score` | float | 0.05 | Minimum deal score (5% = 0.05) |

```json
{
  "deals": [ /* ProductListSerializer objects */ ]
}
```

### Price Drops
`GET /api/price-drops/`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `city_id` | int | 1 | Filter by city |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Results per page (max 50) |

```json
{
  "results": [ /* ProductListSerializer objects */ ],
  "total": 100,
  "page": 1,
  "page_size": 20,
  "total_pages": 5
}
```

### Price Increases
`GET /api/price-increases/`

Same params and response shape as price-drops.

### Discounts
`GET /api/discounts/`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `city_id` | int | 1 | Filter by city |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Results per page (max 50) |
| `min_discount` | float | 5 | Minimum discount percent |
| `canonical_category` | int | — | Filter by category ID |
| `chain_ids` | string | — | Comma-separated chain IDs |
| `sort_by` | string | `discount_percent` | `discount_percent` or `discount_amount` |

```json
{
  "results": [ /* ProductListSerializer objects with discount info */ ],
  "total": 100,
  "page": 1,
  "page_size": 20,
  "total_pages": 5
}
```

---

## Investor Analytics

These endpoints require authenticated users that belong to the `investor` group.

### Investor Stats
`GET /api/investor-stats/`

Returns dashboard payload for the investor admin page.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `no_cache` | bool string | `"false"` | `"true"` to bypass cache and recalculate stats |

**Response:** aggregated analytics object with sections:
- `generated_at`
- `overview`
- `chains`
- `ai_quality`
- `price_intelligence`
- `freshness`
- `price_trends`
- `basket_comparison`
- `social_goods`
- `chain_comparison`

---

## Cart

Guest carts are identified by the `guest_uuid` cookie (set via `/api/session/init/`).

### List Carts
`GET /api/carts/`

```json
{
  "count": 2,
  "results": [
    {
      "uuid": "cart-uuid",
      "name": "My Cart",
      "is_active": true,
      "items": [
        {
          "id": 1,
          "product": { /* ProductListSerializer */ },
          "quantity": 2,
          "added_at": "2024-06-01T10:00:00Z",
          "updated_at": "2024-06-01T10:00:00Z"
        }
      ],
      "items_count": 5,
      "created_at": "2024-06-01T10:00:00Z",
      "updated_at": "2024-06-15T10:00:00Z"
    }
  ]
}
```

### Get Cart
`GET /api/carts/{uuid}/`

Same as cart object in list, plus `is_owner` boolean.

### Create Cart
`POST /api/carts/`

**Body:** `{ "name": "New Cart" }`

### Delete Cart
`DELETE /api/carts/{uuid}/` → 204 No Content

### Update Cart Name
`PATCH /api/carts/{uuid}/update_name/`

**Body:** `{ "name": "Updated Name" }`

```json
{ "cart_uuid": "uuid", "name": "Updated Name" }
```

### Add Item
`POST /api/carts/{uuid}/add_item/`

**Body:**
```json
{ "product_uuid": "product-uuid", "quantity": 2 }
```

Returns CartItem object (201 if new, 200 if quantity updated).

### Remove Item
`POST /api/carts/{uuid}/remove_item/`

**Body:** `{ "product_uuid": "product-uuid" }` → 204 No Content

### Update Item Quantity
`PATCH /api/carts/{uuid}/update_quantity/`

**Body:** `{ "product_uuid": "product-uuid", "quantity": 3 }`

Returns updated CartItem object.

### Quick Add (convenience)
`POST /api/cart/add/`

Auto-creates or uses the active cart.

**Body:** `{ "product_uuid": "product-uuid", "quantity": 1 }`

```json
{ "cart_uuid": "uuid", "item": { /* CartItem */ }, "items_count": 5 }
```

### Archive Cart
`POST /api/carts/{uuid}/archive/`

Archives current cart and creates a new active one.

```json
{ "archived_cart_uuid": "old-uuid", "new_cart_uuid": "new-uuid" }
```

### Set Active Cart
`POST /api/carts/{uuid}/set_active/`

```json
{ "cart_uuid": "uuid", "message": "Cart is now active" }
```

### Cart Summary (optimized)
`GET /api/carts/{uuid}/summary/`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `city_id` | int | 1 | Filter stores by city |

Returns a full cart breakdown with cheapest-per-product, grouped-by-store totals, and unavailable products:

```json
{
  "cart": { /* Cart object */ },
  "total_items": 5,
  "cheapest_per_product": [
    {
      "product": { /* ProductListSerializer */ },
      "quantity": 2,
      "store_id": 1,
      "store_name": "Magnum Almaty",
      "chain_name": "Magnum",
      "chain_logo": "https://...",
      "chain_source": "arbuz",
      "price": 690.0,
      "item_total": 1380.0,
      "currency": "KZT",
      "url": "https://...",
      "ext_product_id": 12345,
      "ext_product_ext_id": "abc-123",
      "ext_product_title": "Молоко Лактель 3,2% 1л",
      "ext_product_image": "https://..."
    }
  ],
  "cheapest_total_price": 4500.0,
  "grouped_by_store": [
    {
      "store_id": 1,
      "store_name": "Magnum Almaty",
      "chain_name": "Magnum",
      "chain_source": "mgo",
      "chain_logo": "https://...",
      "products": [ /* per-store product list, same shape as cheapest_per_product items */ ],
      "store_total": 2500.0
    }
  ],
  "unavailable_products": [
    {
      "product": { /* ProductListSerializer */ },
      "quantity": 1,
      "reason": "Not available in selected stores"
    }
  ],
  "single_store_totals": [
    {
      "store_id": 1,
      "store_name": "Magnum Almaty",
      "chain_name": "Magnum",
      "chain_source": "mgo",
      "chain_logo": "https://...",
      "total_price": 5200.0,
      "available_count": 5,
      "total_count": 5,
      "products": [
        {
          "product": { /* ProductListSerializer */ },
          "quantity": 2,
          "price": 690.0,
          "item_total": 1380.0,
          "currency": "KZT",
          "url": "https://...",
          "ext_product_id": 12345,
          "ext_product_ext_id": "abc-123",
          "ext_product_title": "Молоко Лактель 3,2% 1л",
          "ext_product_image": "https://..."
        }
      ]
    }
  ],
  "selected_stores": [ /* store objects */ ],
  "all_stores": [ /* all stores in city */ ]
}
```

### Transfer Cart to Store
`POST /api/cart/transfer/`

Creates a shareable link for an external store's cart from a list of products. Supported chains: `arbuz`, `airbafresh`, `mgo`.

**Body:**
```json
{
  "chain_source": "arbuz",
  "items": [
    { "ext_id": "12345", "quantity": 2, "title": "Молоко 3,2% 1л", "url": "https://..." },
    { "ext_id": "67890", "quantity": 1 }
  ],
  "city_id": 1
}
```

Each item requires `ext_id` (product ID in the external store). `quantity` defaults to 1. `title` and `url` are optional (used for fallback links).

**Response:**
```json
{
  "chain_source": "arbuz",
  "success": true,
  "cart_url": "https://freedombank.onelink.me/WNLd/abc123",
  "fallback_urls": [
    { "title": "Молоко 3,2% 1л", "url": "https://..." }
  ],
  "items_count": 3,
  "error": null
}
```

For Arbuz, `cart_url` is a shareable onelink that opens a pre-populated cart. If sharing fails, it falls back to the generic cart page URL. When `success` is `false`, `fallback_urls` contains per-product deep links for manual adding. `error` describes the failure reason.

---

## Admin Endpoints

These require `IsAdminUser` permission.

### Merge Products
`POST /api/merge-products/`

**Body:** `{ "product_ids": ["uuid1", "uuid2", "uuid3"] }`

Merges multiple canonical products into one. First UUID becomes the target.

### Unlink Product
`POST /api/unlink-product/`

**Body:** `{ "product_uuid": "uuid", "ext_product_id": 123 }`

Removes a link between an ExtProduct and a canonical Product.

### Unlink and Create Product
`POST /api/unlink-and-create-product/`

Same body as unlink. Unlinks and triggers creation of a new canonical product.

### Relink ExtProduct
`POST /api/relink-ext-product/`

**Body:**
```json
{
  "ext_product_id": 123,
  "source_product_uuid": "uuid1",
  "target_product_uuid": "uuid2"
}
```

Moves a link from one canonical product to another.

### Mark Duplicate ExtProducts
`POST /api/mark-duplicate-ext-products/`

**Body:**
```json
{
  "primary_ext_product_id": 123,
  "duplicate_ext_product_ids": [124, 125]
}
```

### Update Product Title
`PATCH /api/update-product-title/`

**Body:** `{ "product_uuid": "uuid", "new_title": "New Title" }`

### Approve All Links
`POST /api/products/{uuid}/approve-all-links/`

Marks all ProductLinks for this product as manually approved.

### Sync Measure from ExtProducts
`POST /api/products/{uuid}/sync-measure/`

Re-derives measure fields from linked ExtProducts.

---

## Notes for Frontend

- **City filtering**: Most product endpoints accept `city_id` (default 1 = Almaty). Always pass the user's selected city.
- **Pagination**: Product list uses DRF page-based pagination (`page` param, 1-indexed). Search uses Algolia pagination (`page` param, 0-indexed).
- **Currency**: All prices are in KZT.
- **Chain sources**: `mgo`, `arbuz`, `instashop`, `wolt`, `airbafresh` — use these for chain-specific icons/styling.
- **Caching**: Cities, chains, categories are cached server-side (24h). Best deals and price changes are cached (6h).
- **Stock status**: `in_stock` on store entries indicates real-time availability. Products not updated by crawlers for 1+ day are auto-marked out of stock.
