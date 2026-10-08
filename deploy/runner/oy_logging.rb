# Mounted initializer for reviewed Judge0 1.13.1. Payloads are stored only in its DB.
require "logger"

configuration = Rails.application.config
configuration.log_level = :warn
configuration.filter_parameters += [
  :source_code, :stdin, :expected_output, :compile_output, :stderr, :stdout,
  :token, :authn_token, :authn_header, :password, :secret, :authorization,
  :"x-auth-token", :"X-Auth-Token", :query_string
]
# Parameter filters cannot redact arbitrary SQL/exception/Isolate diagnostics.
# Disable application logging rather than promise that WARN alone is sufficient.
configuration.logger = Logger.new(File::NULL)
Rails.logger = configuration.logger
ActiveRecord::Base.logger = nil
Resque.logger = configuration.logger if defined?(Resque)
