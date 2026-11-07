"""Known filesystem contract compatibility preserves sealed authority."""
import asyncio,copy,json
from types import SimpleNamespace
import pytest
from tools.provision_workspace_provider import catalog_for
from eva.providers.mcp_gateway import parse_mcp_gateway_catalog
from eva.adapters.connectors.mcp_gateway import McpGatewayConfig,McpGatewayToolAdapter
from eva.application.conversation import ConversationConflict
from eva.application.schema_validation import validate_schema_value
# Captured public stdio signatures from the installed reference and npm2.2.0.
# Gateway names, identities and all files below are synthetic test data.
_REFERENCE_SIGNATURES = {'create_directory': {'annotations': {'destructiveHint': False,
                                      'idempotentHint': True,
                                      'openWorldHint': False,
                                      'readOnlyHint': False},
                      'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                       'properties': {'path': {'type': 'string'}},
                                       'required': ['path'],
                                       'type': 'object'},
                      'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                        'additionalProperties': False,
                                        'properties': {'content': {'type': 'string'}},
                                        'required': ['content'],
                                        'type': 'object'}},
 'directory_tree': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                    'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                     'properties': {'excludePatterns': {'default': [],
                                                                        'items': {'type': 'string'},
                                                                        'type': 'array'},
                                                    'path': {'type': 'string'}},
                                     'required': ['path'],
                                     'type': 'object'},
                    'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                      'additionalProperties': False,
                                      'properties': {'content': {'type': 'string'}},
                                      'required': ['content'],
                                      'type': 'object'}},
 'edit_file': {'annotations': {'destructiveHint': True,
                               'idempotentHint': False,
                               'openWorldHint': False,
                               'readOnlyHint': False},
               'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                'properties': {'dryRun': {'default': False,
                                                          'description': 'Preview changes using '
                                                                         'git-style diff format',
                                                          'type': 'boolean'},
                                               'edits': {'items': {'properties': {'newText': {'description': 'Text '
                                                                                                             'to '
                                                                                                             'replace '
                                                                                                             'with',
                                                                                              'type': 'string'},
                                                                                  'oldText': {'description': 'Text '
                                                                                                             'to '
                                                                                                             'search '
                                                                                                             'for '
                                                                                                             '- '
                                                                                                             'must '
                                                                                                             'match '
                                                                                                             'exactly',
                                                                                              'type': 'string'}},
                                                                   'required': ['oldText',
                                                                                'newText'],
                                                                   'type': 'object'},
                                                         'type': 'array'},
                                               'path': {'type': 'string'}},
                                'required': ['path', 'edits'],
                                'type': 'object'},
               'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                 'additionalProperties': False,
                                 'properties': {'content': {'type': 'string'}},
                                 'required': ['content'],
                                 'type': 'object'}},
 'get_file_info': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                   'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                    'properties': {'path': {'type': 'string'}},
                                    'required': ['path'],
                                    'type': 'object'},
                   'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                     'additionalProperties': False,
                                     'properties': {'content': {'type': 'string'}},
                                     'required': ['content'],
                                     'type': 'object'}},
 'list_allowed_directories': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                              'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                               'properties': {},
                                               'type': 'object'},
                              'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                                'additionalProperties': False,
                                                'properties': {'content': {'type': 'string'}},
                                                'required': ['content'],
                                                'type': 'object'}},
 'list_directory': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                    'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                     'properties': {'path': {'type': 'string'}},
                                     'required': ['path'],
                                     'type': 'object'},
                    'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                      'additionalProperties': False,
                                      'properties': {'content': {'type': 'string'}},
                                      'required': ['content'],
                                      'type': 'object'}},
 'list_directory_with_sizes': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                               'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                                'properties': {'path': {'type': 'string'},
                                                               'sortBy': {'default': 'name',
                                                                          'description': 'Sort '
                                                                                         'entries '
                                                                                         'by name '
                                                                                         'or size',
                                                                          'enum': ['name', 'size'],
                                                                          'type': 'string'}},
                                                'required': ['path'],
                                                'type': 'object'},
                               'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                                 'additionalProperties': False,
                                                 'properties': {'content': {'type': 'string'}},
                                                 'required': ['content'],
                                                 'type': 'object'}},
 'move_file': {'annotations': {'destructiveHint': True,
                               'idempotentHint': False,
                               'openWorldHint': False,
                               'readOnlyHint': False},
               'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                'properties': {'destination': {'type': 'string'},
                                               'source': {'type': 'string'}},
                                'required': ['source', 'destination'],
                                'type': 'object'},
               'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                 'additionalProperties': False,
                                 'properties': {'content': {'type': 'string'}},
                                 'required': ['content'],
                                 'type': 'object'}},
 'read_file': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
               'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                'properties': {'head': {'description': 'If provided, returns only '
                                                                       'the first N lines of the '
                                                                       'file',
                                                        'type': 'number'},
                                               'path': {'type': 'string'},
                                               'tail': {'description': 'If provided, returns only '
                                                                       'the last N lines of the '
                                                                       'file',
                                                        'type': 'number'}},
                                'required': ['path'],
                                'type': 'object'},
               'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                 'additionalProperties': False,
                                 'properties': {'content': {'type': 'string'}},
                                 'required': ['content'],
                                 'type': 'object'}},
 'read_media_file': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                     'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                      'properties': {'path': {'type': 'string'}},
                                      'required': ['path'],
                                      'type': 'object'},
                     'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                       'additionalProperties': False,
                                       'properties': {'content': {'items': {'anyOf': [{'additionalProperties': False,
                                                                                       'properties': {'data': {'type': 'string'},
                                                                                                      'mimeType': {'type': 'string'},
                                                                                                      'type': {'enum': ['image',
                                                                                                                        'audio'],
                                                                                                               'type': 'string'}},
                                                                                       'required': ['type',
                                                                                                    'data',
                                                                                                    'mimeType'],
                                                                                       'type': 'object'},
                                                                                      {'additionalProperties': False,
                                                                                       'properties': {'resource': {'additionalProperties': False,
                                                                                                                   'properties': {'blob': {'type': 'string'},
                                                                                                                                  'mimeType': {'type': 'string'},
                                                                                                                                  'uri': {'type': 'string'}},
                                                                                                                   'required': ['uri',
                                                                                                                                'blob'],
                                                                                                                   'type': 'object'},
                                                                                                      'type': {'const': 'resource',
                                                                                                               'type': 'string'}},
                                                                                       'required': ['type',
                                                                                                    'resource'],
                                                                                       'type': 'object'}]},
                                                                  'type': 'array'}},
                                       'required': ['content'],
                                       'type': 'object'}},
 'read_multiple_files': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                         'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                          'properties': {'paths': {'description': 'Array of file '
                                                                                  'paths to read. '
                                                                                  'Each path must '
                                                                                  'be a string '
                                                                                  'pointing to a '
                                                                                  'valid file '
                                                                                  'within allowed '
                                                                                  'directories.',
                                                                   'items': {'type': 'string'},
                                                                   'minItems': 1,
                                                                   'type': 'array'}},
                                          'required': ['paths'],
                                          'type': 'object'},
                         'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                           'additionalProperties': False,
                                           'properties': {'content': {'type': 'string'}},
                                           'required': ['content'],
                                           'type': 'object'}},
 'read_text_file': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                    'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                     'properties': {'head': {'description': 'If provided, returns '
                                                                            'only the first N '
                                                                            'lines of the file',
                                                             'type': 'number'},
                                                    'path': {'type': 'string'},
                                                    'tail': {'description': 'If provided, returns '
                                                                            'only the last N lines '
                                                                            'of the file',
                                                             'type': 'number'}},
                                     'required': ['path'],
                                     'type': 'object'},
                    'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                      'additionalProperties': False,
                                      'properties': {'content': {'type': 'string'}},
                                      'required': ['content'],
                                      'type': 'object'}},
 'search_files': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                  'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                   'properties': {'excludePatterns': {'default': [],
                                                                      'items': {'type': 'string'},
                                                                      'type': 'array'},
                                                  'path': {'type': 'string'},
                                                  'pattern': {'type': 'string'}},
                                   'required': ['path', 'pattern'],
                                   'type': 'object'},
                  'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                    'additionalProperties': False,
                                    'properties': {'content': {'type': 'string'}},
                                    'required': ['content'],
                                    'type': 'object'}},
 'write_file': {'annotations': {'destructiveHint': True,
                                'idempotentHint': True,
                                'openWorldHint': False,
                                'readOnlyHint': False},
                'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                 'properties': {'content': {'type': 'string'},
                                                'path': {'type': 'string'}},
                                 'required': ['path', 'content'],
                                 'type': 'object'},
                'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                  'additionalProperties': False,
                                  'properties': {'content': {'type': 'string'}},
                                  'required': ['content'],
                                  'type': 'object'}}}
_CURRENT_SIGNATURES = {'append_file': {'annotations': {'destructiveHint': False,
                                 'idempotentHint': False,
                                 'openWorldHint': False,
                                 'readOnlyHint': False},
                 'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                  'properties': {'content': {'description': 'Text to append, '
                                                                            'exactly as given. No '
                                                                            'newline is added: end '
                                                                            'it with \\n if the '
                                                                            'next append should '
                                                                            'start on a new line.',
                                                             'type': 'string'},
                                                 'path': {'type': 'string'}},
                                  'required': ['path', 'content'],
                                  'type': 'object'},
                 'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                   'additionalProperties': False,
                                   'properties': {'content': {'type': 'string'}},
                                   'required': ['content'],
                                   'type': 'object'}},
 'copy_file': {'annotations': {'destructiveHint': False,
                               'idempotentHint': True,
                               'openWorldHint': False,
                               'readOnlyHint': False},
               'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                'properties': {'destination': {'description': 'Where to create the '
                                                                              'copy; nothing may '
                                                                              'exist there yet',
                                                               'type': 'string'},
                                               'source': {'description': 'The file to copy',
                                                          'type': 'string'}},
                                'required': ['source', 'destination'],
                                'type': 'object'},
               'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                 'additionalProperties': False,
                                 'properties': {'content': {'type': 'string'}},
                                 'required': ['content'],
                                 'type': 'object'}},
 'create_directory': {'annotations': {'destructiveHint': False,
                                      'idempotentHint': True,
                                      'openWorldHint': False,
                                      'readOnlyHint': False},
                      'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                       'properties': {'path': {'type': 'string'}},
                                       'required': ['path'],
                                       'type': 'object'},
                      'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                        'additionalProperties': False,
                                        'properties': {'content': {'type': 'string'}},
                                        'required': ['content'],
                                        'type': 'object'}},
 'directory_tree': {'annotations': {'destructiveHint': False,
                                    'idempotentHint': True,
                                    'openWorldHint': False,
                                    'readOnlyHint': True},
                    'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                     'properties': {'depth': {'description': 'How many levels to '
                                                                             'descend: 1 lists '
                                                                             'only the entries '
                                                                             'directly in path. '
                                                                             'Directories at the '
                                                                             'last level have no '
                                                                             'children array. Omit '
                                                                             'for the whole tree.',
                                                              'exclusiveMinimum': 0,
                                                              'maximum': 9007199254740991,
                                                              'type': 'integer'},
                                                    'excludePatterns': {'default': [],
                                                                        'items': {'type': 'string'},
                                                                        'type': 'array'},
                                                    'path': {'type': 'string'}},
                                     'required': ['path'],
                                     'type': 'object'},
                    'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                      'additionalProperties': False,
                                      'properties': {'content': {'type': 'string'}},
                                      'required': ['content'],
                                      'type': 'object'}},
 'edit_file': {'annotations': {'destructiveHint': True,
                               'idempotentHint': False,
                               'openWorldHint': False,
                               'readOnlyHint': False},
               'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                'properties': {'dryRun': {'default': False,
                                                          'description': 'Preview changes using '
                                                                         'git-style diff format',
                                                          'type': 'boolean'},
                                               'edits': {'items': {'properties': {'newText': {'description': 'Text '
                                                                                                             'to '
                                                                                                             'replace '
                                                                                                             'with',
                                                                                              'type': 'string'},
                                                                                  'oldText': {'description': 'Text '
                                                                                                             'to '
                                                                                                             'search '
                                                                                                             'for '
                                                                                                             '- '
                                                                                                             'must '
                                                                                                             'match '
                                                                                                             'exactly',
                                                                                              'type': 'string'}},
                                                                   'required': ['oldText',
                                                                                'newText'],
                                                                   'type': 'object'},
                                                         'type': 'array'},
                                               'path': {'type': 'string'}},
                                'required': ['path', 'edits'],
                                'type': 'object'},
               'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                 'additionalProperties': False,
                                 'properties': {'content': {'type': 'string'}},
                                 'required': ['content'],
                                 'type': 'object'}},
 'get_file_info': {'annotations': {'destructiveHint': False,
                                   'idempotentHint': True,
                                   'openWorldHint': False,
                                   'readOnlyHint': True},
                   'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                    'properties': {'path': {'type': 'string'}},
                                    'required': ['path'],
                                    'type': 'object'},
                   'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                     'additionalProperties': False,
                                     'properties': {'content': {'type': 'string'}},
                                     'required': ['content'],
                                     'type': 'object'}},
 'list_allowed_directories': {'annotations': {'destructiveHint': False,
                                              'idempotentHint': True,
                                              'openWorldHint': False,
                                              'readOnlyHint': True},
                              'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                               'properties': {},
                                               'type': 'object'},
                              'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                                'additionalProperties': False,
                                                'properties': {'content': {'type': 'string'}},
                                                'required': ['content'],
                                                'type': 'object'}},
 'list_directory': {'annotations': {'destructiveHint': False,
                                    'idempotentHint': True,
                                    'openWorldHint': False,
                                    'readOnlyHint': True},
                    'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                     'properties': {'path': {'type': 'string'}},
                                     'required': ['path'],
                                     'type': 'object'},
                    'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                      'additionalProperties': False,
                                      'properties': {'content': {'type': 'string'}},
                                      'required': ['content'],
                                      'type': 'object'}},
 'list_directory_with_sizes': {'annotations': {'destructiveHint': False,
                                               'idempotentHint': True,
                                               'openWorldHint': False,
                                               'readOnlyHint': True},
                               'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                                'properties': {'path': {'type': 'string'},
                                                               'sortBy': {'default': 'name',
                                                                          'description': 'Sort '
                                                                                         'entries '
                                                                                         'by name '
                                                                                         'or size',
                                                                          'enum': ['name', 'size'],
                                                                          'type': 'string'}},
                                                'required': ['path'],
                                                'type': 'object'},
                               'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                                 'additionalProperties': False,
                                                 'properties': {'content': {'type': 'string'}},
                                                 'required': ['content'],
                                                 'type': 'object'}},
 'move_file': {'annotations': {'destructiveHint': True,
                               'idempotentHint': False,
                               'openWorldHint': False,
                               'readOnlyHint': False},
               'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                'properties': {'destination': {'type': 'string'},
                                               'source': {'type': 'string'}},
                                'required': ['source', 'destination'],
                                'type': 'object'},
               'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                 'additionalProperties': False,
                                 'properties': {'content': {'type': 'string'}},
                                 'required': ['content'],
                                 'type': 'object'}},
 'read_file': {'annotations': {'destructiveHint': False,
                               'idempotentHint': True,
                               'openWorldHint': False,
                               'readOnlyHint': True},
               'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                'properties': {'head': {'description': 'If provided, returns only '
                                                                       'the first N lines of the '
                                                                       'file',
                                                        'maximum': 9007199254740991,
                                                        'minimum': 0,
                                                        'type': 'integer'},
                                               'path': {'type': 'string'},
                                               'tail': {'description': 'If provided, returns only '
                                                                       'the last N lines of the '
                                                                       'file',
                                                        'maximum': 9007199254740991,
                                                        'minimum': 0,
                                                        'type': 'integer'}},
                                'required': ['path'],
                                'type': 'object'},
               'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                 'additionalProperties': False,
                                 'properties': {'content': {'type': 'string'}},
                                 'required': ['content'],
                                 'type': 'object'}},
 'read_media_file': {'annotations': {'destructiveHint': False,
                                     'idempotentHint': True,
                                     'openWorldHint': False,
                                     'readOnlyHint': True},
                     'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                      'properties': {'path': {'type': 'string'}},
                                      'required': ['path'],
                                      'type': 'object'},
                     'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                       'additionalProperties': False,
                                       'properties': {'content': {'items': {'anyOf': [{'additionalProperties': False,
                                                                                       'properties': {'data': {'type': 'string'},
                                                                                                      'mimeType': {'type': 'string'},
                                                                                                      'type': {'enum': ['image',
                                                                                                                        'audio'],
                                                                                                               'type': 'string'}},
                                                                                       'required': ['type',
                                                                                                    'data',
                                                                                                    'mimeType'],
                                                                                       'type': 'object'},
                                                                                      {'additionalProperties': False,
                                                                                       'properties': {'resource': {'additionalProperties': False,
                                                                                                                   'properties': {'blob': {'type': 'string'},
                                                                                                                                  'mimeType': {'type': 'string'},
                                                                                                                                  'uri': {'type': 'string'}},
                                                                                                                   'required': ['uri',
                                                                                                                                'blob'],
                                                                                                                   'type': 'object'},
                                                                                                      'type': {'const': 'resource',
                                                                                                               'type': 'string'}},
                                                                                       'required': ['type',
                                                                                                    'resource'],
                                                                                       'type': 'object'}]},
                                                                  'type': 'array'}},
                                       'required': ['content'],
                                       'type': 'object'}},
 'read_multiple_files': {'annotations': {'destructiveHint': False,
                                         'idempotentHint': True,
                                         'openWorldHint': False,
                                         'readOnlyHint': True},
                         'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                          'properties': {'paths': {'description': 'Array of file '
                                                                                  'paths to read. '
                                                                                  'Each path must '
                                                                                  'be a string '
                                                                                  'pointing to a '
                                                                                  'valid file '
                                                                                  'within allowed '
                                                                                  'directories.',
                                                                   'items': {'type': 'string'},
                                                                   'minItems': 1,
                                                                   'type': 'array'}},
                                          'required': ['paths'],
                                          'type': 'object'},
                         'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                           'additionalProperties': False,
                                           'properties': {'content': {'type': 'string'}},
                                           'required': ['content'],
                                           'type': 'object'}},
 'read_text_file': {'annotations': {'destructiveHint': False,
                                    'idempotentHint': True,
                                    'openWorldHint': False,
                                    'readOnlyHint': True},
                    'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                     'properties': {'head': {'description': 'If provided, returns '
                                                                            'only the first N '
                                                                            'lines of the file',
                                                             'maximum': 9007199254740991,
                                                             'minimum': 0,
                                                             'type': 'integer'},
                                                    'path': {'type': 'string'},
                                                    'tail': {'description': 'If provided, returns '
                                                                            'only the last N lines '
                                                                            'of the file',
                                                             'maximum': 9007199254740991,
                                                             'minimum': 0,
                                                             'type': 'integer'}},
                                     'required': ['path'],
                                     'type': 'object'},
                    'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                      'additionalProperties': False,
                                      'properties': {'content': {'type': 'string'}},
                                      'required': ['content'],
                                      'type': 'object'}},
 'search_files': {'annotations': {'destructiveHint': False,
                                  'idempotentHint': True,
                                  'openWorldHint': False,
                                  'readOnlyHint': True},
                  'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                   'properties': {'excludePatterns': {'default': [],
                                                                      'items': {'type': 'string'},
                                                                      'type': 'array'},
                                                  'path': {'type': 'string'},
                                                  'pattern': {'type': 'string'}},
                                   'required': ['path', 'pattern'],
                                   'type': 'object'},
                  'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                    'additionalProperties': False,
                                    'properties': {'content': {'type': 'string'}},
                                    'required': ['content'],
                                    'type': 'object'}},
 'write_file': {'annotations': {'destructiveHint': True,
                                'idempotentHint': True,
                                'openWorldHint': False,
                                'readOnlyHint': False},
                'input_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                 'properties': {'content': {'type': 'string'},
                                                'path': {'type': 'string'}},
                                 'required': ['path', 'content'],
                                 'type': 'object'},
                'output_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                  'additionalProperties': False,
                                  'properties': {'content': {'type': 'string'}},
                                  'required': ['content'],
                                  'type': 'object'}}}

@pytest.fixture
def fixture():
 old=[]
 for name,signature in _REFERENCE_SIGNATURES.items():
  old.append(dict(original_name=name,name='synthetic-'+name,
   gateway_id='synthetic-local-provider',integration_type='MCP',
   request_type='STREAMABLEHTTP',enabled=True,reachable=True,
   deprecated=False,visibility='private',**copy.deepcopy(signature)))
 selected={r['original_name']:r for r in old}
 specs=parse_mcp_gateway_catalog(json.dumps(catalog_for(selected,include_reads=True)))
 current=copy.deepcopy(old)
 for row in current:row.update(copy.deepcopy(_CURRENT_SIGNATURES[row['original_name']]))
 public={name:{'annotations':value['annotations'],'inputSchema':value['input_schema'],
               'outputSchema':value['output_schema']} for name,value in _CURRENT_SIGNATURES.items()}
 return old,current,specs,public

def adapter(rows,specs):
 result=McpGatewayToolAdapter(McpGatewayConfig('https://example.test','synthetic'),specs)
 result._request_json=lambda method,path,body: {'tools':rows,'nextCursor':None}
 return result

def test_original_catalog_authority_stays_accepted(fixture):
 old,_,specs,_=fixture
 a=adapter(old,specs)
 assert len(asyncio.run(a.verify_catalog()))==5

def test_exact_new_public_contract_recognizes_only_the_original_five_grants(fixture):
 _,current,specs,_=fixture;a=adapter(current,specs)
 expected={s.execute_catalog_name:s.execute_catalog_sha256 for s in specs}
 assert asyncio.run(a.verify_catalog())==expected
 assert len(a.conversation_tools(specs))==5
 assert set(a.planner().action_names)=={s.descriptor.name for s in specs}
 read=next(s for s in specs if s.descriptor.name=='read_workspace_document')
 actual=a._verified_input_schemas[(read.execute_tool_name,'execute')]
 assert actual['properties']['head']['type']=='integer'
 with pytest.raises(ConversationConflict):validate_schema_value({'path':'/proof-state/synthetic','head':1.5},actual,'arguments',provider_schema=True)


def test_upstream_two_extra_routes_never_become_admitted_actions(fixture):
 _,current,specs,public=fixture
 for name in ['append_file','copy_file']:
  row=copy.deepcopy(current[0]);row.update(name='not-admitted-'+name,original_name=name)
  for a,b in [('input_schema','inputSchema'),('output_schema','outputSchema'),('annotations','annotations')]:row[a]=copy.deepcopy(public[name][b])
  current.append(row)
 a=adapter(current,specs);assert len(asyncio.run(a.verify_catalog()))==5
 for name in ['append_file','copy_file']:
  with pytest.raises(ConversationConflict):asyncio.run(a.planner().prepare(None,SimpleNamespace(name=name,arguments={})))


@pytest.mark.parametrize('damage',['partial-old','unknown-original','missing-tool','duplicate-original','new-input','new-output','new-annotation','other-gateway','changed-route','disabled','unreachable','new-visibility'])
def test_unknown_or_unsealed_catalog_remains_rejected_and_clears_admission(fixture,damage):
 old,current,specs,_=fixture;a=adapter(current,specs);asyncio.run(a.verify_catalog())
 row=next(r for r in current if r['original_name']=='write_file')
 if damage=='partial-old':row.update(copy.deepcopy(next(r for r in old if r['original_name']=='write_file')))
 elif damage=='unknown-original':row['original_name']='other_vendor_write'
 elif damage=='missing-tool':current.remove(next(r for r in current if r['original_name']=='get_file_info'))
 elif damage=='duplicate-original':current.append(copy.deepcopy(current[0]))
 elif damage=='new-input':row['input_schema']['properties']['extra']={'type':'string'}
 elif damage=='new-output':row['output_schema']['properties']['extra']={'type':'string'}
 elif damage=='new-annotation':row['annotations']['unrecognized']=True
 elif damage=='other-gateway':row['gateway_id']='synthetic-other-gateway'
 elif damage=='changed-route':row['name']='different-route'
 elif damage=='disabled':row['enabled']=False
 elif damage=='unreachable':row['reachable']=False
 elif damage=='new-visibility':row['visibility']='team' if row['visibility']!='team' else 'private'
 with pytest.raises(RuntimeError):asyncio.run(a.verify_catalog())
 with pytest.raises(RuntimeError):a.planner()


@pytest.mark.parametrize('head',[{'type':'number','minimum':1,'maximum':1000},{'type':'integer','minimum':-1,'maximum':1000},{'type':'integer','minimum':0,'maximum':9007199254740992},{'type':'integer'}])
def test_unproved_old_numeric_domains_are_not_declared_compatible(fixture,head):
 _,current,specs,_=fixture
 for s in specs:
  if s.descriptor.name=='read_workspace_document':s.descriptor.input_schema['properties']['head']=head
 a=adapter(current,specs)
 with pytest.raises(RuntimeError):asyncio.run(a.verify_catalog())


def directory_specs(old, specs):
 from eva.adapters.connectors.mcp_gateway import mcp_gateway_catalog_binding
 from eva.contracts.canonical_json import canonical_sha256
 row=next(r for r in old if r['original_name']=='create_directory')
 catalog=catalog_for({r['original_name']:r for r in old},include_reads=True)
 definition=copy.deepcopy(catalog['tools'][0])
 definition.update(eva_name='create_workspace_directory',description='Create an admitted workspace directory',
  completion_guidance='Use the captured provider result without claiming independent verification',
  input_schema={'type':'object','properties':{'path':{'type':'string','maxLength':1024}},'required':['path'],'additionalProperties':False},
  operation_kind='invoke',execute_catalog_name=row['name'],execute_tool_name=row['name'],
  observe_catalog_name=row['name'],observe_tool_name=row['name'],
  execute_catalog_sha256=canonical_sha256(mcp_gateway_catalog_binding(row,contract='EvaMcpGatewayProvider/v4')),
  observe_catalog_sha256=canonical_sha256(mcp_gateway_catalog_binding(row,contract='EvaMcpGatewayProvider/v4')),
  execute_argument_bindings=[{'argument_name':'path','provider_name':'path'}],
  observe_argument_bindings=[{'argument_name':'path','provider_name':'path'}],proof_bindings=[])
 return (*specs,*parse_mcp_gateway_catalog(json.dumps({'contract':'EvaMcpGatewayProvider/v4','tools':[definition]})))

def test_existing_sixth_directory_admission_keeps_its_original_hash_and_domain(fixture):
 old,current,specs,_=fixture;specs=directory_specs(old,specs)
 baseline=asyncio.run(adapter(old,specs).verify_catalog())
 candidate=adapter(current,specs)
 assert asyncio.run(candidate.verify_catalog())==baseline
 assert len(candidate.planner().action_names)==6
 assert 'create_workspace_directory' in candidate.planner().action_names
 # Recognizing a signature alone cannot add the separately admitted directory action.
 original=adapter(current,specs[:5]);asyncio.run(original.verify_catalog())
 assert 'create_workspace_directory' not in original.planner().action_names

def test_directory_compatibility_never_rebinds_a_changed_route(fixture):
 old,current,specs,_=fixture;specs=directory_specs(old,specs)
 row=next(r for r in current if r['original_name']=='create_directory');row['name']='unsealed-directory-route'
 with pytest.raises(RuntimeError):asyncio.run(adapter(current,specs).verify_catalog())
