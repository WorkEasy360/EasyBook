import { paramOf, type RawSearchParams } from "@/lib/list-query";
import type { ThreeWayMatchQuery } from "@/types/api/purchases";

/**
 * Tolerances for the three-way match endpoints, read from the page URL.
 *
 * ThreeWayMatchQuerySerializer takes `quantity_tolerance` (4 dp) and
 * `price_tolerance` (2 dp), both ≥ 0 and defaulting to 0 = exact. Anything
 * the serializer would reject is dropped here, so a hand-edited URL shows an
 * exact match instead of a validation error panel.
 */
const QUANTITY = /^\d{1,14}(\.\d{1,4})?$/;
const PRICE = /^\d{1,16}(\.\d{1,2})?$/;

export function toleranceQuery(searchParams: RawSearchParams): ThreeWayMatchQuery {
  const query: ThreeWayMatchQuery = {};
  const quantity = paramOf(searchParams, "quantity_tolerance");
  const price = paramOf(searchParams, "price_tolerance");
  if (quantity && QUANTITY.test(quantity)) query.quantity_tolerance = quantity;
  if (price && PRICE.test(price)) query.price_tolerance = price;
  return query;
}
