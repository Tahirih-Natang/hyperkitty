# -*- coding: utf-8 -*-
# Copyright (C) 2014-2015 by the Free Software Foundation, Inc.
#
# This file is part of HyperKitty.
#
# HyperKitty is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# HyperKitty is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# HyperKitty.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Aurelien Bompard <abompard@fedoraproject.org>
#

from __future__ import absolute_import, print_function, unicode_literals

from django.contrib.auth.models import User
from mock import Mock

from hyperkitty.lib import mailman
from hyperkitty.lib.cache import cache
from hyperkitty.models import MailingList, Sender
from hyperkitty.tests.utils import TestCase


class MailmanSubscribeTestCase(TestCase):

    def setUp(self):
        self.ml = mailman.FakeMMList("list@example.com")
        self.mailman_client.get_list.side_effect = lambda n: self.ml
        self.ml.get_member = Mock()
        self.ml.subscribe = Mock()
        self.user = User.objects.create_user(
            'testuser', 'test@example.com', 'testPass')

    def test_subscribe_already_subscribed(self):
        self.ml.settings["subscription_policy"] = "open"
        mailman.subscribe("list@example.com", self.user)
        self.assertTrue(self.ml.get_member.called)
        self.assertFalse(self.ml.subscribe.called)

    def test_subscribe_not_subscribed(self):
        self.ml.settings["subscription_policy"] = "open"
        self.ml.get_member.side_effect = ValueError
        cache.set("User:%s:subscriptions" % self.user.id, "test-value")
        class Prefs(dict):
            save = Mock()
        member = Mock()
        member.preferences = Prefs()
        self.ml.subscribe.side_effect = lambda *a, **kw: member
        mailman.subscribe("list@example.com", self.user)
        self.assertTrue(self.ml.get_member.called)
        self.ml.subscribe.assert_called_with(
            'test@example.com', ' ', pre_verified=True, pre_confirmed=True)
        self.assertEqual(member.preferences["delivery_status"], "by_user")
        self.assertTrue(member.preferences.save.called)
        self.assertEqual(cache.get("User:%s:subscriptions" % self.user.id),
                         None)

    def test_subscribe_moderate(self):
        self.ml.get_member.side_effect = ValueError # User is not subscribed
        self.ml.settings["subscription_policy"] = "moderate"
        self.assertRaises(mailman.ModeratedListException,
                          mailman.subscribe, "list@example.com", self.user)
        self.assertFalse(self.ml.subscribe.called)
        self.ml.settings["subscription_policy"] = "confirm_then_moderate"
        self.assertRaises(mailman.ModeratedListException,
                          mailman.subscribe, "list@example.com", self.user)
        self.assertFalse(self.ml.subscribe.called)

    def test_subscribe_already_subscribed_moderated(self):
        # Subscribing to a moderated list a user is already subscribed to
        # should just do nothing
        self.ml.settings["subscription_policy"] = "moderate"
        try:
            mailman.subscribe("list@example.com", self.user)
        except mailman.ModeratedListException:
            self.fail("A ModeratedListException was raised")
        self.assertFalse(self.ml.subscribe.called)
        self.ml.settings["subscription_policy"] = "confirm_then_moderate"
        try:
            mailman.subscribe("list@example.com", self.user)
        except mailman.ModeratedListException:
            self.fail("A ModeratedListException was raised")
        self.assertFalse(self.ml.subscribe.called)

    def test_subscribe_moderate_undetected(self):
        # The list requires moderation but we failed to detect it in the
        # possible subscription policies. If the subscription requires a
        # confirmation, Mailman will reply with a 202 code, and mailman.client
        # will return the response content (a dict) instead of a Member
        # instance. Make sure we can handle that.
        self.ml.settings["subscription_policy"] = "open"
        self.ml.get_member.side_effect = ValueError
        response_dict = {'token_owner': 'subscriber', 'http_etag': '"deadbeef"',
                         'token': 'deadbeefdeadbeef'}
        self.ml.subscribe.side_effect = lambda *a, **kw: response_dict
        try:
            mailman.subscribe("list@example.com", self.user)
        except AttributeError:
            self.fail("This use case was not properly handled")
        self.assertTrue(self.ml.get_member.called)
        # There must be no exception even if the response is not a Member.



class MailmanSyncTestCase(TestCase):

    def test_call_update_from_mailman(self):
        for i in range(10):
            MailingList.objects.create(name="test%s@example.com" % i)
        mailman.sync_with_mailman()
        # Track calls to MailingList.update_from_mailman() using the client's
        # get_list() method. Didn't find anything better.
        self.assertEqual(self.mailman_client.get_list.call_count, 10)

    def test_call_set_mailman_id(self):
        mock_user = Mock()
        mock_user.user_id = "from-mailman"
        self.mailman_client.get_user.side_effect = lambda a: mock_user
        for i in range(10):
            Sender.objects.create(address="test%s@example.com" % i)
        for i in range(10, 20):
            Sender.objects.create(address="test%s@example.com" % i,
                                  mailman_id="already-set")
        mailman.sync_with_mailman()
        # Track calls to Sender.set_mailman_id() using the client's
        # get_user() method. Didn't find anything better.
        # Only senders with a mailman_id to None should have been considered.
        self.assertEqual(self.mailman_client.get_user.call_count, 10)
        self.assertEqual(
            Sender.objects.filter(mailman_id__isnull=True).count(), 0)
        self.assertEqual(
            Sender.objects.filter(mailman_id="from-mailman").count(), 10)
        self.assertEqual(
            Sender.objects.filter(mailman_id="already-set").count(), 10)
