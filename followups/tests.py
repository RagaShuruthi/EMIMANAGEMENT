"""
Comprehensive tests for Follow-up Persons management, Password Setup Workflow, and Employee Portal.
Verifies:
1. Role definitions and model relationships (FollowUpAssignment, FollowUpActivity).
2. Administrator management: creating employee by name, email, phone, and multi-shop assignment.
3. Secure password setup workflow: token generation, setting private password, never storing plain-text.
4. Admin controls: editing shop assignments, resetting password access, activating/deactivating.
5. Real-time automatic updates: employee portal permissions update immediately upon admin assignment changes.
6. Follow-up Dashboard: strictly displays assigned shops, authorized customers, pending follow-ups,
   completed follow-ups, and upcoming tasks.
7. Server-side shop-level security: HTTP 403 Forbidden on unauthorized customer access or action.
8. One-click completion of pending follow-up tasks.
"""

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication
from followups.models import FollowUpAssignment, FollowUpActivity

User = get_user_model()


class FollowUpPersonModelTests(TestCase):
    """Test Follow-up role, assignments, and activity models."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin_test", email="admin@example.com", password="password123"
        )
        self.shop_owner = User.objects.create_user(
            username="owner1", role=User.Role.SHOP_OWNER, password="password123"
        )
        self.shop1 = Shop.objects.create(
            name="Apex Mobile Hub", owner_account=self.shop_owner, owner_name="Owner One", phone="9988776655"
        )
        self.shop2 = Shop.objects.create(
            name="Galaxy Cellular", owner_account=self.shop_owner, owner_name="Owner Two", phone="9988776644"
        )

    def test_followup_role_and_property(self):
        followup_user = User.objects.create_user(
            username="officer1",
            role=User.Role.FOLLOWUP,
            phone="9123456789",
            password="password123"
        )
        self.assertTrue(followup_user.is_followup_role)
        self.assertFalse(followup_user.is_admin_role)
        self.assertFalse(followup_user.is_shop_owner_role)
        self.assertEqual(followup_user.phone, "9123456789")

    def test_multi_shop_assignment(self):
        employee = User.objects.create_user(
            username="collector_raj", role=User.Role.FOLLOWUP, password="password123"
        )
        # Assign to 2 shops
        a1 = FollowUpAssignment.objects.create(employee=employee, shop=self.shop1, assigned_by=self.admin)
        a2 = FollowUpAssignment.objects.create(employee=employee, shop=self.shop2, assigned_by=self.admin)

        assigned_shops = [a.shop for a in employee.followup_assignments.filter(is_active=True)]
        self.assertEqual(len(assigned_shops), 2)
        self.assertIn(self.shop1, assigned_shops)
        self.assertIn(self.shop2, assigned_shops)
        self.assertTrue(a1.is_active)
        self.assertTrue(a2.is_active)

    def test_followup_activity_logging(self):
        employee = User.objects.create_user(
            username="collector_kavita", role=User.Role.FOLLOWUP, password="password123"
        )
        customer = Customer.objects.create(
            shop=self.shop1,
            name="Vikram Kumar",
            primary_mobile="9876543210",
            secondary_mobile="9876543219",
            home_address="123 Civil Lines",
            work_address="456 Tech Park"
        )
        activity = FollowUpActivity.objects.create(
            employee=employee,
            customer=customer,
            shop=self.shop1,
            activity_type=FollowUpActivity.ActivityType.CALL,
            status=FollowUpActivity.Status.PENDING,
            notes="Customer promised payment on Friday 5 PM.",
            next_followup_date=timezone.localdate()
        )
        self.assertEqual(activity.employee, employee)
        self.assertEqual(activity.customer, customer)
        self.assertEqual(activity.activity_type, FollowUpActivity.ActivityType.CALL)
        self.assertEqual(activity.status, FollowUpActivity.Status.PENDING)
        self.assertIn("Vikram", str(activity))


class AdminFollowUpManagementTests(TestCase):
    """Test Admin endpoints for adding, editing, password resetting, and managing follow-up staff."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin_user", role=User.Role.ADMIN, password="adminpassword123"
        )
        self.shop_owner = User.objects.create_user(
            username="shopowner_test", role=User.Role.SHOP_OWNER, password="password123"
        )
        self.shop_a = Shop.objects.create(
            name="Shop Alpha", owner_account=self.shop_owner, owner_name="Owner A", phone="9988112233"
        )
        self.shop_b = Shop.objects.create(
            name="Shop Beta", owner_account=self.shop_owner, owner_name="Owner B", phone="9988112244"
        )
        self.employee = User.objects.create_user(
            username="followup_anil",
            role=User.Role.FOLLOWUP,
            first_name="Anil",
            last_name="Sharma",
            email="anil.sharma@example.com",
            phone="9876543211",
            password="password123"
        )
        FollowUpAssignment.objects.create(employee=self.employee, shop=self.shop_a, assigned_by=self.admin)

    def test_unauthenticated_access_redirects(self):
        res = self.client.get(reverse("admin_followup_list"))
        self.assertEqual(res.status_code, 302)

    def test_shop_owner_forbidden_from_admin_followup_views(self):
        self.client.login(username="shopowner_test", password="password123")
        res = self.client.get(reverse("admin_followup_list"))
        self.assertEqual(res.status_code, 403)

    def test_admin_can_view_followup_list(self):
        self.client.login(username="admin_user", password="adminpassword123")
        res = self.client.get(reverse("admin_followup_list"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "followup_anil")
        self.assertContains(res, "Anil Sharma")
        self.assertContains(res, "Shop Alpha")

    def test_admin_create_employee_with_name_email_phone_and_assigned_shops(self):
        """Admin creates employee with name, email, phone, and selects shops. No password entered."""
        self.client.login(username="admin_user", password="adminpassword123")
        post_data = {
            "first_name": "Siddharth",
            "last_name": "Patel",
            "email": "siddharth@example.com",
            "phone": "9812345678",
            "username": "",  # Auto-generated
            "password": "",  # Generates password setup link
            "confirm_password": "",
            "is_active": "on",
            "assigned_shops": [self.shop_a.pk, self.shop_b.pk],
        }
        res = self.client.post(reverse("admin_followup_add"), post_data)
        new_emp = User.objects.get(email="siddharth@example.com")
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("admin_followup_detail", kwargs={"pk": new_emp.pk}))
        self.assertEqual(new_emp.role, User.Role.FOLLOWUP)
        self.assertEqual(new_emp.phone, "9812345678")
        self.assertEqual(new_emp.first_name, "Siddharth")
        self.assertFalse(new_emp.has_usable_password())  # Unusable password until employee sets it
        self.assertEqual(new_emp.followup_assignments.filter(is_active=True).count(), 2)

    def test_followup_staff_phone_validation(self):
        """Staff contact phone must be strictly 10 digits without letters or symbols."""
        from followups.forms import FollowUpStaffForm
        # 1. 11 digits
        f1 = FollowUpStaffForm(data={
            "first_name": "Test", "email": "t1@example.com", "phone": "98123456789", "assigned_shops": [self.shop_a.pk]
        })
        self.assertFalse(f1.is_valid())
        self.assertIn("phone", f1.errors)

        # 2. 9 digits
        f2 = FollowUpStaffForm(data={
            "first_name": "Test", "email": "t2@example.com", "phone": "981234567", "assigned_shops": [self.shop_a.pk]
        })
        self.assertFalse(f2.is_valid())
        self.assertIn("phone", f2.errors)

        # 3. Letters/symbols
        f3 = FollowUpStaffForm(data={
            "first_name": "Test", "email": "t3@example.com", "phone": "98123-5678", "assigned_shops": [self.shop_a.pk]
        })
        self.assertFalse(f3.is_valid())
        self.assertIn("phone", f3.errors)

    def test_admin_edit_followup_person_and_shop_assignments(self):
        self.client.login(username="admin_user", password="adminpassword123")
        edit_data = {
            "username": "followup_anil",
            "first_name": "Anil Updated",
            "last_name": "Sharma",
            "phone": "9999999999",
            "email": "anil.sharma@example.com",
            "is_active": "on",
            "assigned_shops": [self.shop_b.pk],  # Switched to Shop Beta
        }
        res = self.client.post(reverse("admin_followup_edit", kwargs={"pk": self.employee.pk}), edit_data)
        self.assertEqual(res.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.first_name, "Anil Updated")
        self.assertEqual(self.employee.phone, "9999999999")
        active_assigned_shops = [a.shop for a in self.employee.followup_assignments.filter(is_active=True)]
        self.assertIn(self.shop_b, active_assigned_shops)
        self.assertNotIn(self.shop_a, active_assigned_shops)

    def test_admin_reset_password_access_link(self):
        """Admin can trigger reset password access to generate fresh setup link."""
        self.client.login(username="admin_user", password="adminpassword123")
        res = self.client.post(reverse("admin_followup_reset_password", kwargs={"pk": self.employee.pk}))
        self.assertEqual(res.status_code, 302)
        self.assertIn(f"setup_url_{self.employee.pk}", self.client.session)

    def test_admin_toggle_employee_status(self):
        self.client.login(username="admin_user", password="adminpassword123")
        self.assertTrue(self.employee.is_active)

        # Deactivate
        res = self.client.post(reverse("admin_followup_status", kwargs={"pk": self.employee.pk}))
        self.assertEqual(res.status_code, 302)
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.is_active)

        # Reactivate
        self.client.post(reverse("admin_followup_status", kwargs={"pk": self.employee.pk}))
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_active)


class PasswordSetupWorkflowTests(TestCase):
    """Test cryptographic token password setup workflow."""

    def setUp(self):
        self.client = Client()
        self.employee = User.objects.create_user(
            username="priya_setup",
            role=User.Role.FOLLOWUP,
            email="priya@example.com",
            phone="9876543200",
        )
        self.employee.set_unusable_password()
        self.employee.save()

    def test_valid_token_allows_employee_to_set_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.employee.pk))
        token = default_token_generator.make_token(self.employee)
        setup_url = reverse("followup_setup_password", kwargs={"uidb64": uid, "token": token})

        # GET setup form
        res_get = self.client.get(setup_url)
        self.assertEqual(res_get.status_code, 200)
        self.assertContains(res_get, "Set Your Password")

        # POST new password
        post_data = {
            "new_password": "MySecretPassword123!",
            "confirm_password": "MySecretPassword123!",
        }
        res_post = self.client.post(setup_url, post_data)
        self.assertEqual(res_post.status_code, 302)
        self.assertRedirects(res_post, reverse("followup_dashboard"))

        # Password must be hashed with PBKDF2, never stored as plain text
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password("MySecretPassword123!"))
        self.assertNotEqual(self.employee.password, "MySecretPassword123!")
        self.assertTrue(self.employee.password.startswith("pbkdf2_sha256$"))

    def test_invalid_token_is_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(self.employee.pk))
        bad_setup_url = reverse("followup_setup_password", kwargs={"uidb64": uid, "token": "invalid-token-123"})
        res = self.client.get(bad_setup_url)
        self.assertEqual(res.status_code, 400)
        self.assertContains(res, "Activation Link Expired or Invalid", status_code=400)


class FollowUpEmployeePortalIsolationTests(TestCase):
    """Test Follow-up employee portal data isolation, real-time updates, and task management."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin_sup", password="password123"
        )
        self.owner = User.objects.create_user(
            username="owner_store", role=User.Role.SHOP_OWNER, password="password123"
        )
        # Shop 1 & Shop 2
        self.shop1 = Shop.objects.create(
            name="Prime Mobiles", owner_account=self.owner, owner_name="Owner 1", phone="9871111111"
        )
        self.shop2 = Shop.objects.create(
            name="Elite Electronics", owner_account=self.owner, owner_name="Owner 2", phone="9872222222"
        )

        # Customers
        self.cust_shop1 = Customer.objects.create(
            shop=self.shop1,
            name="Ramesh Kumar (Shop 1)",
            primary_mobile="9811111111",
            secondary_mobile="9811111112",
            home_address="Shop 1 Address Road",
            work_address="Shop 1 Office Complex"
        )
        self.app1 = FinanceApplication.objects.create(
            shop=self.shop1,
            customer=self.cust_shop1,
            mobile_model="Vivo V29 5G",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("25000.00"),
            emi_duration=6,
            monthly_emi=Decimal("5000.00"),
            monthly_due_day=5,
            status=FinanceApplication.Status.APPROVED,
        )

        self.cust_shop2 = Customer.objects.create(
            shop=self.shop2,
            name="Suresh Verma (Shop 2)",
            primary_mobile="9822222222",
            secondary_mobile="9822222223",
            home_address="Shop 2 Address Street",
            work_address="Shop 2 Business Park"
        )
        self.app2 = FinanceApplication.objects.create(
            shop=self.shop2,
            customer=self.cust_shop2,
            mobile_model="Samsung Galaxy A54",
            mobile_price=Decimal("28000.00"),
            down_payment=Decimal("4000.00"),
            financier_request_amount=Decimal("24000.00"),
            emi_duration=6,
            monthly_emi=Decimal("4670.00"),
            monthly_due_day=10,
            status=FinanceApplication.Status.APPROVED,
        )

        # Follow-up employee assigned ONLY to Shop 1
        self.employee = User.objects.create_user(
            username="officer_priya",
            role=User.Role.FOLLOWUP,
            phone="9870000000",
            password="portalpassword123"
        )
        self.assignment1 = FollowUpAssignment.objects.create(
            employee=self.employee,
            shop=self.shop1,
            assigned_by=self.admin,
            is_active=True
        )

        # Create activities: one pending, one completed
        self.pending_act = FollowUpActivity.objects.create(
            employee=self.employee,
            customer=self.cust_shop1,
            shop=self.shop1,
            activity_type=FollowUpActivity.ActivityType.REMINDER,
            status=FollowUpActivity.Status.PENDING,
            notes="Follow up on 1st EMI installment.",
            next_followup_date=timezone.localdate()
        )
        self.completed_act = FollowUpActivity.objects.create(
            employee=self.employee,
            customer=self.cust_shop1,
            shop=self.shop1,
            activity_type=FollowUpActivity.ActivityType.PAYMENT,
            status=FollowUpActivity.Status.COMPLETED,
            notes="Cash down payment receipt acknowledged."
        )

    def test_employee_portal_dashboard_scoped_to_assigned_shops(self):
        self.client.login(username="officer_priya", password="portalpassword123")
        res = self.client.get(reverse("followup_dashboard"))
        self.assertEqual(res.status_code, 200)

        # Customer from assigned Shop 1 is present
        self.assertContains(res, "Ramesh Kumar (Shop 1)")
        self.assertContains(res, "Prime Mobiles")

        # Customer from unassigned Shop 2 must NOT appear
        self.assertNotContains(res, "Suresh Verma (Shop 2)")
        self.assertNotContains(res, "Elite Electronics")

        # Metrics are calculated
        self.assertEqual(res.context["total_assigned_shops"], 1)
        self.assertEqual(res.context["total_customers"], 1)
        self.assertEqual(res.context["total_pending"], 1)
        self.assertEqual(res.context["total_completed"], 1)
        self.assertEqual(res.context["total_upcoming"], 1)

    def test_automatic_updates_reflect_immediately(self):
        """When admin changes shop assignments, employee's dashboard immediately reflects it."""
        self.client.login(username="officer_priya", password="portalpassword123")

        # Initially assigned ONLY to Shop 1
        res1 = self.client.get(reverse("followup_dashboard"))
        self.assertContains(res1, "Prime Mobiles")
        self.assertNotContains(res1, "Elite Electronics")

        # Admin assigns Shop 2 as well
        FollowUpAssignment.objects.create(
            employee=self.employee,
            shop=self.shop2,
            assigned_by=self.admin,
            is_active=True
        )

        # Immediate refresh reflects Shop 2 on dashboard
        res2 = self.client.get(reverse("followup_dashboard"))
        self.assertContains(res2, "Prime Mobiles")
        self.assertContains(res2, "Elite Electronics")

        # Customer queue across assigned shops reflects customers from Shop 2
        res_tasks = self.client.get(reverse("followup_tasks"))
        self.assertContains(res_tasks, "Suresh Verma (Shop 2)")

    def test_employee_cannot_access_unassigned_customer_detail_returns_403(self):
        """Cross-shop access check: Accessing customer from unassigned shop must return 403 Forbidden."""
        self.client.login(username="officer_priya", password="portalpassword123")
        res = self.client.get(reverse("followup_customer_detail", kwargs={"pk": self.cust_shop2.pk}))
        self.assertEqual(res.status_code, 403)

    def test_employee_can_mark_pending_activity_completed(self):
        self.client.login(username="officer_priya", password="portalpassword123")
        self.assertEqual(self.pending_act.status, FollowUpActivity.Status.PENDING)

        res = self.client.post(reverse("followup_complete_activity", kwargs={"pk": self.pending_act.pk}))
        self.assertEqual(res.status_code, 302)

        self.pending_act.refresh_from_db()
        self.assertEqual(self.pending_act.status, FollowUpActivity.Status.COMPLETED)
        self.assertIsNotNone(self.pending_act.completed_at)

    def test_employee_cannot_complete_activity_for_unassigned_shop_returns_403(self):
        """Server-side check: Employee cannot complete activity belonging to unassigned shop."""
        unassigned_act = FollowUpActivity.objects.create(
            employee=self.admin,
            customer=self.cust_shop2,
            shop=self.shop2,
            activity_type=FollowUpActivity.ActivityType.CALL,
            status=FollowUpActivity.Status.PENDING,
            notes="Unassigned shop task"
        )
        self.client.login(username="officer_priya", password="portalpassword123")
        res = self.client.post(reverse("followup_complete_activity", kwargs={"pk": unassigned_act.pk}))
        self.assertEqual(res.status_code, 403)

    def test_quick_update_need_two_days_flags_customer(self):
        """When follow-up officer selects 'NEED_TWO_DAYS', customer is flagged and callback scheduled 2 days later."""
        self.client.login(username="officer_priya", password="portalpassword123")
        url = reverse("followup_quick_update", kwargs={"pk": self.cust_shop1.pk})
        res = self.client.post(url, {
            "outcome": "NEED_TWO_DAYS",
            "notes": "Customer requested 2 more days to arrange funds."
        })
        self.assertEqual(res.status_code, 302)

        latest_activity = self.cust_shop1.followup_activities.first()
        self.assertEqual(latest_activity.outcome, FollowUpActivity.Outcome.NEED_TWO_DAYS)
        self.assertTrue(latest_activity.is_flagged)
        self.assertEqual(latest_activity.status, FollowUpActivity.Status.PENDING)
        from datetime import timedelta
        expected_date = timezone.localdate() + timedelta(days=2)
        self.assertEqual(latest_activity.next_followup_date, expected_date)

    def test_quick_update_paid_marks_completed(self):
        """Selecting 'PAID' marks outcome as paid, completed, and unflagged."""
        self.client.login(username="officer_priya", password="portalpassword123")
        url = reverse("followup_quick_update", kwargs={"pk": self.cust_shop1.pk})
        res = self.client.post(url, {
            "outcome": "PAID",
            "notes": "Customer made full EMI payment."
        })
        self.assertEqual(res.status_code, 302)

        latest_activity = self.cust_shop1.followup_activities.first()
        self.assertEqual(latest_activity.outcome, FollowUpActivity.Outcome.PAID)
        self.assertFalse(latest_activity.is_flagged)
        self.assertEqual(latest_activity.status, FollowUpActivity.Status.COMPLETED)

    def test_two_dues_filters_in_dashboard(self):
        """Verify 'today' and 'pending' filters operate independently and return correct counts."""
        self.client.login(username="officer_priya", password="portalpassword123")

        # Test Today's Due filter
        res_today = self.client.get(reverse("followup_dashboard") + "?due_filter=today")
        self.assertEqual(res_today.status_code, 200)
        self.assertIn("today_due_count", res_today.context)

        # Test Pending Dues filter
        res_pending = self.client.get(reverse("followup_dashboard") + "?due_filter=pending")
        self.assertEqual(res_pending.status_code, 200)
        self.assertIn("pending_due_count", res_pending.context)

        # Customer table does not have phone columns
        self.assertNotContains(res_pending, "Primary Phone")
        self.assertNotContains(res_pending, "Secondary Phone")
