# Copyright 2017 The Nuclio Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import logging
import unittest.mock

import nuclio_sdk


class Platform(object):

    def __init__(self):
        self._logger = nuclio_sdk.Logger(logging.DEBUG)
        self._logger.set_handler('default', sys.stdout, nuclio_sdk.logger.HumanReadableFormatter())

        self._handler_contexts = {}
        self._call_function_mock = unittest.mock.MagicMock()
        self._kind = 'test'

        # for tests that need a context
        self._context = nuclio_sdk.Context(self._logger, self)

    def call_handler(self, handler, event):
        return handler(self._get_handler_context(handler), event)

    def call_function(self, name, event):
        return self._call_function_mock(name, event)

    def get_call_function_call_args(self, index):
        return self._call_function_mock.call_args_list[index][0]

    @property
    def context(self):
        return self._context

    @property
    def kind(self):
        return self._kind

    @property
    def call_function_mock(self):
        return self._call_function_mock

    def _get_handler_context(self, handler):
        try:
            return self._handler_contexts[handler]
        except KeyError:

            # first time we're calling this handler
            context = nuclio_sdk.Context(self._logger, self)

            # get handler module
            handler_module = sys.modules[handler.__module__]

            self._logger.info_with('Calling handler init context', handler=str(handler))

            # call init context
            if hasattr(handler_module, 'init_context'):
                getattr(handler_module, 'init_context')(context)

            # save context and return it
            self._handler_contexts[handler] = context

            return context