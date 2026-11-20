-- Guests can browse the menu; only customers with verified SMS may order.
-- Correct the former guest-checkout flag without changing KPS or SMS channels.
UPDATE app_settings
   SET settings=jsonb_set(settings,'{customer_phone_required}','true'::jsonb,true)
 WHERE brand_id='enbishan';
