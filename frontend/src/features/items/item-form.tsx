"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput, QuantityInput } from "@/components/ui/numeric-input";
import { AccountPicker } from "@/features/shared/account-picker";
import { useToast } from "@/components/ui/toast";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { Item, ItemInput, UnitOfMeasure } from "@/types/api/items";

/**
 * Create / edit an item.
 *
 * The one rule that shapes this form: only a PRODUCT can track inventory
 * (spec §37). Choosing "Service" therefore hides the stock fields and clears
 * them, rather than leaving a reorder level attached to something that has no
 * stock.
 */

const schema = z
  .object({
    sku: z.string().min(1, "An SKU is required.").max(64),
    name: z.string().min(1, "A name is required.").max(255),
    description: z.string().max(2000).optional(),
    item_type: z.enum(["product", "service"]),
    unit: z.string().min(1, "Choose a unit."),
    track_inventory: z.boolean(),
    is_sellable: z.boolean(),
    is_purchasable: z.boolean(),
    sales_price: z.string(),
    purchase_price: z.string(),
    reorder_level: z.string(),
    tax_category: z.string().max(64).optional(),
    sales_account: z.string().nullable(),
    purchase_account: z.string().nullable(),
    inventory_account: z.string().nullable(),
    cogs_account: z.string().nullable(),
    is_active: z.boolean(),
  })
  .refine((values) => !(values.item_type === "service" && values.track_inventory), {
    message: "A service cannot track inventory.",
    path: ["track_inventory"],
  });

type ItemFormValues = z.infer<typeof schema>;

function toFormValues(item: Item | null): ItemFormValues {
  return {
    sku: item?.sku ?? "",
    name: item?.name ?? "",
    description: item?.description ?? "",
    item_type: item?.item_type ?? "product",
    unit: item?.unit ?? "",
    track_inventory: item?.track_inventory ?? true,
    is_sellable: item?.is_sellable ?? true,
    is_purchasable: item?.is_purchasable ?? true,
    sales_price: item?.sales_price ?? "",
    purchase_price: item?.purchase_price ?? "",
    reorder_level: item?.reorder_level ?? "0",
    tax_category: item?.tax_category ?? "",
    sales_account: item?.sales_account ?? null,
    purchase_account: item?.purchase_account ?? null,
    inventory_account: item?.inventory_account ?? null,
    cogs_account: item?.cogs_account ?? null,
    is_active: item?.is_active ?? true,
  };
}

export function ItemForm({ item, units }: { item?: Item | null; units: UnitOfMeasure[] }) {
  const router = useRouter();
  const toast = useToast();
  const isEdit = Boolean(item);

  const [formError, setFormError] = React.useState<string | null>(null);
  const [reference, setReference] = React.useState<string | null>(null);

  const {
    control,
    register,
    handleSubmit,
    setError,
    setValue,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<ItemFormValues>({
    resolver: zodResolver(schema),
    defaultValues: toFormValues(item ?? null),
  });

  useUnsavedChanges(isDirty && !isSubmitting);

  const itemType = useWatch({ control, name: "item_type" });
  const trackInventory = useWatch({ control, name: "track_inventory" });
  const isProduct = itemType === "product";

  // Switching to a service clears the stock settings instead of leaving a
  // reorder level on something that has no stock.
  const [lastType, setLastType] = React.useState(itemType);
  if (itemType !== lastType) {
    setLastType(itemType);
    if (itemType === "service" && trackInventory) {
      setValue("track_inventory", false, { shouldDirty: true });
      setValue("reorder_level", "0", { shouldDirty: true });
      setValue("inventory_account", null, { shouldDirty: true });
    }
  }

  const mutation = useApiMutation<Item, ItemInput>(
    "items",
    (input) =>
      isEdit && item
        ? api.patch<Item>(`items/${item.id}`, input)
        : api.post<Item>("items", input),
    {
      onSuccess: (saved) => {
        toast.push({
          tone: "success",
          title: isEdit ? "Item updated" : "Item created",
          description: saved.name,
        });
        router.push(`/items/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError(formErrorOf(error));
        setReference(referenceOf(error));
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (messages[0]) {
            setError(field as keyof ItemFormValues, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: ItemFormValues) {
    setFormError(null);
    setReference(null);

    mutation.mutate({
      sku: values.sku,
      name: values.name,
      description: values.description ?? "",
      item_type: values.item_type,
      unit: values.unit,
      track_inventory: values.item_type === "product" ? values.track_inventory : false,
      is_sellable: values.is_sellable,
      is_purchasable: values.is_purchasable,
      // "" means "not set"; the API expects null rather than an empty string.
      sales_price: values.sales_price === "" ? null : values.sales_price,
      purchase_price: values.purchase_price === "" ? null : values.purchase_price,
      reorder_level: values.reorder_level === "" ? "0" : values.reorder_level,
      tax_category: values.tax_category ?? "",
      sales_account: values.sales_account,
      purchase_account: values.purchase_account,
      inventory_account: values.inventory_account,
      cogs_account: values.cogs_account,
      is_active: values.is_active,
    });
  }

  return (
    <form
      method="post"
      onSubmit={(event) => void handleSubmit(onSubmit)(event)}
      className="flex flex-col gap-4"
      noValidate
    >
      <FormError message={formError} reference={reference} />

      <Card>
        <CardHeader title="Details" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Name" required error={errors.name?.message ?? null}>
            <Input autoFocus {...register("name")} />
          </FormField>

          <FormField label="SKU" required error={errors.sku?.message ?? null}>
            <Input className="tabular" {...register("sku")} />
          </FormField>

          <FormField label="Type" required error={errors.item_type?.message ?? null}>
            <Select {...register("item_type")}>
              <option value="product">Product</option>
              <option value="service">Service</option>
            </Select>
          </FormField>

          <FormField label="Unit" required error={errors.unit?.message ?? null}>
            <Select placeholder="Choose a unit" {...register("unit")}>
              {units.map((unit) => (
                <option key={unit.id} value={unit.id}>
                  {unit.name} ({unit.symbol || unit.code})
                </option>
              ))}
            </Select>
          </FormField>

          <FormField label="Description" className="sm:col-span-2">
            <Textarea rows={2} {...register("description")} />
          </FormField>

          <FormField
            label="Tax category"
            hint="Used to pick the GST rate on documents."
            error={errors.tax_category?.message ?? null}
          >
            <Input {...register("tax_category")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Pricing" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Sales price" error={errors.sales_price?.message ?? null}>
            <Controller
              control={control}
              name="sales_price"
              render={({ field }) => (
                <NumericInput value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>

          <FormField label="Purchase price" error={errors.purchase_price?.message ?? null}>
            <Controller
              control={control}
              name="purchase_price"
              render={({ field }) => (
                <NumericInput value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>

          <Checkbox label="Can be sold" {...register("is_sellable")} />
          <Checkbox label="Can be purchased" {...register("is_purchasable")} />
        </CardBody>
      </Card>

      {isProduct ? (
        <Card>
          <CardHeader
            title="Inventory"
            description="Only products can hold stock."
          />
          <CardBody className="flex flex-col gap-4">
            <Checkbox
              label="Track inventory"
              hint="Stock moves only through posted documents and adjustments."
              {...register("track_inventory")}
            />
            {errors.track_inventory?.message ? (
              <p className="text-xs text-danger-600">{errors.track_inventory.message}</p>
            ) : null}

            {trackInventory ? (
              <FormField
                label="Reorder level"
                hint="Below this, the item appears in Low stock."
                error={errors.reorder_level?.message ?? null}
                className="max-w-xs"
              >
                <Controller
                  control={control}
                  name="reorder_level"
                  render={({ field }) => (
                    <QuantityInput value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                  )}
                />
              </FormField>
            ) : null}
          </CardBody>
        </Card>
      ) : null}

      <Card>
        {/*
          There is no organization-level default account in the backend:
          invoicing an item with no sales account is rejected
          (item_missing_sales_account), and a tracked item with no inventory
          account cannot be billed or adjusted. Saying "leave blank for the
          default" would promise a fallback that does not exist.
        */}
        <CardHeader
          title="Accounts"
          description="Where this item posts. An item cannot be invoiced without a sales account, and a tracked item needs an inventory account before stock can move."
        />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Sales account">
            <Controller
              control={control}
              name="sales_account"
              render={({ field }) => (
                <AccountPicker value={field.value} onChange={field.onChange} accountType="income" />
              )}
            />
          </FormField>

          <FormField label="Purchase account">
            <Controller
              control={control}
              name="purchase_account"
              render={({ field }) => (
                <AccountPicker value={field.value} onChange={field.onChange} accountType="expense" />
              )}
            />
          </FormField>

          {isProduct && trackInventory ? (
            <>
              <FormField label="Inventory account">
                <Controller
              control={control}
              name="inventory_account"
              render={({ field }) => (
                <AccountPicker value={field.value} onChange={field.onChange} accountType="asset" />
              )}
            />
              </FormField>

              <FormField label="Cost of goods sold account">
                <Controller
              control={control}
              name="cogs_account"
              render={({ field }) => (
                <AccountPicker value={field.value} onChange={field.onChange} accountType="expense" />
              )}
            />
              </FormField>
            </>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <Checkbox
            label="Active"
            hint="Inactive items stay on past documents but cannot be added to new ones."
            {...register("is_active")}
          />
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending}>
          {isEdit ? "Save changes" : "Create item"}
        </Button>
      </div>
    </form>
  );
}
