## CamelTrunk – Free vs Premium & Trunk Assist Functionality

Developer + Operations Specification | V1 Membership & Shopping Workflow

## 1. FINAL PREMIUM BENEFITS

Premium is for regular CamelTrunk users. Benefits should be automatic, visible and simple.

| Feature | FREE PLAN | PREMIUM PLAN |
| --- | --- | --- |
| Trunk ID | Included | Included |
| Storage | Standard Free-plan rules | Premium storage benefits/rules |
| Trunk Assist | Standard service charge | 25% OFF CamelTrunk service charge |
| Local delivery | Actual charge | Actual charge; no 25% discount |
| International shipping | Standard pricing | 5% OFF eligible shipping charge |
| Consolidation | Standard paid rate | FREE |
| Basic packing | Standard paid rate | FREE |
| Other basic services | Standard charges | FREE where configured as a Premium benefit |
| Priority Trunk Assist | Standard queue | Priority processing |
| Customer support | Standard | Priority support |
| Member offers | No | Yes |

## 2. DISCOUNT BOUNDARIES

25% Trunk Assist discount: applies only to CamelTrunk's service charge. It does not apply to product value, local delivery, taxes, store charges, alteration charges or other third-party expenses.

5% shipping discount: applies only to the eligible CamelTrunk shipping charge. It does not apply to customs duty, destination taxes or other third-party charges unless explicitly configured.

FREE consolidation/packing/basic services: configure each eligible service explicitly. Do not make third-party expenses free.

## 3. ACCOUNT TYPE & STORAGE INDICATIONS

| Location | FREE ACCOUNT | PREMIUM ACCOUNT |
| --- | --- | --- |
| Dashboard | FREE badge + Upgrade to Premium | PREMIUM badge |
| Trunk page | Show Free storage status/days + Upgrade CTA | Show Premium storage status/days |
| Storage charges | Show standard rate + Upgrade CTA | Show Premium rate/benefit |
| Before expiry | Countdown + Premium saving message | Countdown + member benefit |
| After expiry | Current daily charge + Upgrade CTA | Premium daily charge/benefit |

Developer rule: account type must be visible wherever storage charges are displayed.

## 4. FREE-TO-PREMIUM UPSELL PLACEMENT

| Page / Area | Recommended functionality |
| --- | --- |
| Dashboard | Contextual Premium banner: save on Trunk Assist, shipping and eligible services. |
| Trunk / Storage | Show Premium storage benefit + Upgrade. |
| Trunk Assist landing page | Show standard fee and Premium saving: “Premium saves 25%.” |
| Each Trunk Assist card | Show standard service fee and Premium service fee where practical. |
| Quotation screen | Show standard fee, Premium discount and final fee. |


| Page / Area | Recommended functionality |
| --- | --- |
| Shipping quotation | Show 5% Premium saving + Upgrade for Free users. |
| Consolidation / Packing | Show “FREE with Premium” where applicable. |
| Checkout | Show Upgrade before payment when an upgrade changes eligible pricing. |
| Account / Profile | Permanent Premium upgrade option. |

Use contextual, non-blocking upsells. Do not show aggressive pop-ups on every page.


## 5. WHERE TO DISPLAY TRUNK ASSIST PRICES

Customers should see CamelTrunk's service charges before submitting a request, wherever the price can be determined.

| Location | Display |
| --- | --- |
| Trunk Assist main page | Service categories + standard/starting fee + Premium saving. |
| Individual service page | Exact service-charge rule, inclusions/exclusions and examples. |
| Request form | Applicable service-charge information box. |
| Quotation | Product cost + local delivery + service charge + Premium discount + total. |
| Checkout | Final payable amount + refundable/non-refundable components. |
| Order history | Original charge + discount + paid amount + refund status. |

## 6. WHEN TO COLLECT MONEY FOR TRUNK ASSIST

Separate service charge from product value and third-party costs. For sourcing services, do not purchase before customer approval and successful payment.

| Service | Before research | After quote / approval | Execution |
| --- | --- | --- | --- |
| Product Link | No purchase payment. | Collect product value + local delivery | Purchase after payment. |
|   |   | + service charge. |   |
| Image / Cart Screenshot | No purchase payment. | Research; then collect product value | Purchase after payment. |
|   |   | + local delivery + service charge. |   |
| Local Shop | Normally no payment before | Collect confirmed product value + | Purchase after payment. |
|   | confirmation. | local delivery + service charge. |   |
| Boutique / Alteration | Requirements first. | Collect confirmed product + alteration | Proceed after payment. |
|   |   | + local delivery + service charge. |   |
| Custom Request | Request details first. | Quote after feasibility; collect | Proceed after payment. |
|   |   | approved order value + service |   |
|   |   | charge. |   |

## 7. REFUNDABLE vs NON-REFUNDABLE

| Component | Default | Rule |
| --- | --- | --- |
| Product value; purchase not made | REFUNDABLE | Refund if funds have not been committed and cancellation is |
|   |   | allowed. |
| Local delivery; not incurred | REFUNDABLE | Refund if seller/courier has not charged it. |
| Product already purchased | NOT automatically | Subject to seller's cancellation/return/refund policy. |
|   | refundable |   |
| Trunk Assist service charge after | NON-REFUNDABLE | Research, sourcing, availability checking or quotation work has |
| meaningful work |   | been performed. |
| Service charge before work starts | REFUNDABLE where | If no paid work has started and policy permits cancellation. |
|   | allowed |   |
| Third-party costs already incurred | NON-REFUNDABLE | Actual external charges already incurred are deducted. |
| Eligible unused balance | REFUNDABLE | Return remaining eligible amount after deductions. |

Final Terms & Conditions should define exact cancellation windows, seller-dependent refunds and exceptional cases.

## 8. RECOMMENDED PAYMENT WORKFLOW

| Step | Customer sees | System action |
| --- | --- | --- |
| 1. Request | Request received. | Create request; identify Free/Premium. |
| 2. Research | Researching / sourcing. | Record product availability and costs. |
| 3. Quote | Item + local delivery + service fee + discount + | Lock quote for validity period. |
|   | total. |   |


| Step | Customer sees | System action |
| --- | --- | --- |
| 4. Approval | Approve / Reject. | If rejected, apply cancellation rule. |
| 5. Payment | Final payable amount. | Collect before purchase. |
| 6. Purchase | Order in progress. | Record seller invoice/order ID. |
| 7. Parcel received | Parcel added to Trunk. | Update inventory. |
| 8. Shipment | Shipping + Premium discount. | Collect shipping payment before dispatch. |

## 9. PREMIUM CALCULATION

## Premium Trunk Assist fee = Standard Trunk Assist fee × 75%.

Example: ■500 standard service charge ■125 discount ■375 Premium service charge. Local delivery and product value remain unchanged.

## 10. DEVELOPER NOTES

| Function | Requirement |
| --- | --- |
| Membership flag | Every account has FREE or PREMIUM status. |
| Automatic pricing | Apply eligible benefits automatically; no coupon required. |
| 25% Trunk Assist | Only the CamelTrunk service-charge component. |
| 5% shipping | Only eligible shipping-charge component. |
| Free services | Eligible line items become and show “Premium benefit”. |
| Quotation | Show original amount, discount, final amount and excluded charges. |
| Price lock | Approved quotation retains price for stated validity period. |
| Upgrade recalculation | If Free user upgrades before payment, recalculate eligible charges. |
| Refund engine | Store components separately for accurate refunds. |
| Audit trail | Store original fee, discount, payment, refund and reason. |

## 11. CUSTOMER-FACING COPY

Free: “Use CamelTrunk with standard pay-as-you-go pricing.”

Premium: “Save 25% on Trunk Assist service charges, 5% on eligible shipping charges, and enjoy FREE consolidation, packing and selected basic services.”

Important: “Premium discounts apply to eligible CamelTrunk charges only. Product prices, local delivery, customs, taxes and other third-party charges are excluded unless stated otherwise.”

## 12. V1 IMPLEMENTATION CHECKLIST

- Create FREE and PREMIUM account types.

- Show account badge on dashboard and relevant service pages.

- Use a central pricing engine instead of hard-coded prices on individual pages.

- Show standard price and Premium saving wherever applicable.

- Apply 25% Trunk Assist discount automatically.

- Apply 5% eligible shipping discount automatically.

- Set eligible consolidation, packing and basic services to FREE for Premium.

- Show quotation breakdown: original fee, discount, final fee.

- Separate product value, local delivery and CamelTrunk service fee.

- Implement refundable/non-refundable status by component.

- Add contextual Upgrade to Premium CTAs for Free users.


- Recalculate eligible charges after Premium upgrade before payment.

- Record payment/refund history.

Final principle: Premium should be valuable for frequent users while protecting CamelTrunk margins. Apply discounts only to CamelTrunk-controlled revenue and make every charge transparent.
