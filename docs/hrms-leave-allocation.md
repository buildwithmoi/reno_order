# HRMS debugging: prorated event-based leave for a mid-year joiner (Part 13)

**Setup:**
- **Annual Leave** is prorated by joining date.
- **Maternity, Paternity and Marriage Leave** have fixed entitlements.

**Symptom:** an employee who joins mid-year gets a *prorated* allocation for an event-based leave. For example, 2 days of Marriage Leave instead of 3.

## Root-cause analysis
**Reproduce:** one Leave Policy with all four leave types; a Leave Period from 1 Jan to 31 Dec 2026; an employee joining **1 July 2026**; a Leave Policy Assignment *based on Leave Period*; submit. Result: Annual 12 of 24 (expected), but **Marriage 2 of 3** (wrong). This is captured as a test: `test_without_the_flag_hrms_prorates_it_reproducing_the_bug`.

**Relevant HRMS v16 logic** (`hrms/hr/doctype/leave_policy_assignment/leave_policy_assignment.py`):

1. Submitting the assignment calls `grant_leave_alloc_for_employee()`. That loops over every Leave Policy Detail and calls `create_leave_allocation()`, which calls **`get_new_leaves()`**:
   ```python
   if leave_details.is_compensatory:
   	new_leaves_allocated = 0
   elif leave_details.is_earned_leave and current_date < getdate(self.effective_to):
   	new_leaves_allocated = self.get_leaves_for_passed_period(...)
   else:
   	# calculate pro-rated leaves for other leave types
   	new_leaves_allocated = calculate_pro_rated_leaves(
   		annual_allocation, date_of_joining, self.effective_from, self.effective_to, is_earned_leave=False
   	)
   ```
2. **`calculate_pro_rated_leaves()`**:
   ```python
   if not leaves or getdate(date_of_joining) <= getdate(period_start_date):
   	return leaves
   actual_period = date_diff(period_end_date, date_of_joining) + 1
   complete_period = date_diff(period_end_date, period_start_date) + 1
   leaves *= actual_period / complete_period
   return rounded(leaves)
   ```

**Root cause:** every leave type that isn't *earned* or *compensatory* is prorated whenever the employee joined after the period start. HRMS has **no per-leave-type way to say "this entitlement is fixed"**. So a fixed, event-based leave in the same policy as Annual Leave gets cut exactly like Annual Leave: 3 × 184/365 = 1.5 → rounded to 2.

**What doesn't fix it:**
- **Two policies** (Annual by Leave Period, events by Joining Date): HRMS's `validate_policy_assignment_overlap()` rejects **any** overlapping submitted assignment for the same employee, whatever the policy.
- **Assigning everyone "based on Joining Date":** this stops the proration for *Annual Leave* too, which breaks the stated requirement.
- **Marking the event leaves as "Earned Leave":** they'd then accrue monthly, which is wrong for an event entitlement.

## Proposed solution
**Option A: configuration only (no code).** Take the event-based types out of the yearly Leave Policy, and allocate them **when the event happens**: a Leave Allocation of the fixed entitlement, created by HR when a marriage or birth is recorded. This is arguably the most accurate HR model, because these leaves are tied to an event, not to a year. The cost is a manual step per event, which could be automated later.

**Option B: a small customization (implemented here).** Use this if the business wants the yearly policy to keep granting these entitlements up front:
1. A Leave Type checkbox, **Fixed Entitlement (not pro-rated)**, added as a custom field by `setup/install.py`. It's only added when HRMS is installed, including when HRMS is installed *later* (`after_app_install`).
2. A subclass registered through the official **`override_doctype_class`** hook (`reno_order/hr/leave_policy_assignment.py`):
   ```python
   class RenoLeavePolicyAssignment(LeavePolicyAssignment):
   	def get_new_leaves(self, annual_allocation, leave_details, date_of_joining):
   		if frappe.get_cached_value("Leave Type", leave_details.name, "fixed_entitlement"):
   			return flt(annual_allocation)
   		return super().get_new_leaves(annual_allocation, leave_details, date_of_joining)
   ```
3. A validation stops the flag being set on earned or compensatory leave types.

**Configuration or customization?** Option A is configuration. Option B needs a *small* customization, but **no HRMS core change**: one overridden method that defers to HRMS for everything else, plus one custom field. Upgrades stay safe. The one caveat with `override_doctype_class` is that only one app can override a given doctype. If another installed app also overrides Leave Policy Assignment, merge the two into a single subclass. Applying the flag to past allocations is a separate, deliberate step: cancel and re-submit the affected assignments, or adjust the allocations manually.

## Test cases (`reno_order/tests/test_hrms_leave.py`, run when HRMS is installed)
| Test | Asserts |
|---|---|
| `test_mid_year_joiner_gets_full_fixed_entitlement` | Joining 1 July: Annual **12** (still prorated); Maternity **84**, Paternity **5**, Marriage **3** (full) |
| `test_without_the_flag_hrms_prorates_it_reproducing_the_bug` | Without the flag, Marriage Leave = **2**: the reported bug, reproduced |
| `test_late_joiner_still_gets_fixed_entitlement` | Joining 29 December: Annual rounds to 0 and HRMS skips it, but Marriage Leave is still **3** |
| `test_full_year_employee_unaffected` | Joining before the period: everything is full (no regression) |
| `test_flag_rejected_on_earned_leave` | The flag can't be combined with earned leave |

All pass on a site with HRMS 16.20. CI installs HRMS so they run on every push.
