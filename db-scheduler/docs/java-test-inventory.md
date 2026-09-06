# db-scheduler: every test scenario

Machine-extracted test method names, grouped by class. These are the behaviours the port must reproduce.


**Total: 246 test methods across the suite.**


## `jdbc/CommitWhenAutocommitDisabledTest.java`  (2 tests)

- `test_overridden_autocommit_behavior`
- `test_transactions`

## `jdbc/JdbcRunnerTest.java`  (7 tests)

- `test_basics`
- `test_map_multiple_rows`
- `test_batch_insert`
- `should_map_constraint_violations_to_custom_exception_for_primary_key_constraint`
- `should_map_constraint_violations_to_custom_exception_for_unique_constraint_`
- `test_in_transction`
- `nested_in_transction_not_allowed`

## `jdbc/TransactionManagerTest.java`  (5 tests)

- `should_commit_if_no_exceptions`
- `should_rollback_if_exception`
- `should_rollback_if_sql_exception_on_commit`
- `should_close_on_exception_on_commit_and_rollback`
- `should_restore_autocommit_if_enabled_on_connection_open`

## `scheduler/CustomTableNameTest.java`  (1 tests)

- `can_customize_table_name`

## `scheduler/DeadExecutionsTest.java`  (2 tests)

- `scheduler_should_handle_dead_executions`
- `scheduler_should_detect_dead_execution_that_never_updated_heartbeat`

## `scheduler/DueExecutionsBatchTest.java`  (1 tests)

- `test_trigger_check_for_more_due`

## `scheduler/ExceptionUtilsTest.java`  (3 tests)

- `exceptionWithNoMessageDescribedAsItsClassSimpleName`
- `exceptionDescribedAsClassSimpleNameAndMessage`
- `describingNullIsNull`

## `scheduler/ExecutionTest.java`  (1 tests)

- `test_equals`

## `scheduler/HeartbeatStateTest.java`  (3 tests)

- `happy`
- `success_resets_failing`
- `not_stale_until_tolerance_passed`

## `scheduler/JdbcTaskRepositoryTest.java`  (30 tests)

- `test_createIfNotExists`
- `test_createBatch`
- `test_createBatch_fails_if_any_execution_already_exists`
- `test_replace`
- `get_due_should_only_include_due_executions`
- `get_due_should_honor_max_results_limit`
- `get_due_should_be_sorted`
- `get_due_should_be_sorted_by_priority_when_enabled`
- `get_due_should_not_include_previously_unresolved`
- `picked_executions_should_not_be_returned_as_due`
- `picked_execution_should_have_information_about_which_scheduler_processes_it`
- `should_not_be_able_to_pick_execution_that_has_been_rescheduled`
- `reschedule_should_move_execution_in_time`
- `reschedule_should_persist_consecutive_failures`
- `reschedule_should_update_data_if_specified`
- `test_get_failing_executions`
- `get_failing_executions_should_not_return_previously_failed_but_currently_successful`
- `get_scheduled_executions`
- `summary_by_task_name`
- `get_scheduled_by_task_name`
- `get_dead_executions_should_not_include_previously_unresolved`
- `get_scheduled_executions_should_work_with_unresolved`
- `lockAndGetDue_should_pick_due`
- `lockAndGetDue_should_not_include_previously_unresolved`
- `lockAndFetchGeneric_happy`
- `should_release_unresolved_tasks`
- `unpickPickedBatch_should_log_warn_if_one_of_tasks_is_not_picked`
- `unpickPickedBatch_should_handle_empty_collection_gracefully`
- `unpickPickedBatch_should_not_crash_when_Picked_batch_task_not_in_database`
- `task_data_should_be_deserialized_once_and_shared_when_accessed_concurrently`

## `scheduler/RunUntilShutdownTest.java`  (2 tests)

- `should_wait_on_ok_execution`
- `should_wait_on_runtime_exception`

## `scheduler/ScheduledExecutionTest.java`  (3 tests)

- `test_equals`
- `test_data_class_type_equals`
- `test_data_class_type_not_equals`

## `scheduler/SchedulerClientExceptionsTest.java`  (4 tests)

- `failsToRescheduleWhenTaskIsNotFound`
- `failsToRescheduleWhenATaskIsPickedAndExecuting`
- `failsToCancelWhenTaskIsNotFound`
- `failsToCancelWhenATaskIsPickedAndExecuting`

## `scheduler/SchedulerClientTest.java`  (9 tests)

- `client_should_be_able_to_schedule_executions`
- `client_should_be_able_to_schedule_batch_executions`
- `should_be_able_to_schedule_other_executions_from_an_executionhandler`
- `client_should_be_able_to_fetch_executions_for_task`
- `client_should_be_able_to_fetch_single_scheduled_execution`
- `client_should_be_able_to_reschedule_executions`
- `client_should_be_able_to_reschedule_executions_via_schedule_when_exists_reschedule`
- `client_should_do_nothing_when_task_instance_exists_and_schedule_with_when_exists_do_nothing`
- `raw_client_should_be_able_to_fetch_executions`

## `scheduler/SchedulerTest.java`  (10 tests)

- `scheduler_should_execute_task_when_exactly_due`
- `scheduler_should_execute_rescheduled_task_when_exactly_due`
- `scheduler_should_not_execute_canceled_tasks`
- `scheduler_should_execute_recurring_task_and_reschedule`
- `scheduler_should_track_duration`
- `should_expose_cause_of_failure_to_completion_handler`
- `should_only_attempt_task_when_max_retries_handler_used`
- `should_reschedule_failure_on_exponential_backoff_with_default_rate`
- `should_reschedule_failure_on_exponential_backoff_with_defined_rate`
- `should_unpick_unresolved_tasks_during_rolling_update`

## `scheduler/SerializerExceptionsTest.java`  (2 tests)

- `onDeserializeFailureWillThrowExpectedException`
- `onSerializeFailureWillThrowExpectedException`

## `scheduler/StringUtilsTest.java`  (1 tests)

- `test_truncate`

## `scheduler/TaskResolverTest.java`  (5 tests)

- `shouldProduceUnresolvedTaskEventWhenTheTaskResolverIsUnableToResolveTheTask`
- `shouldReturnUnresolvedTasksWhenRequested`
- `shouldReturnUnresolvedTasksOlderThanSpecifiedDuration`
- `shouldClearUnresolvedTaskWhenRequested`
- `shouldNotClearOtherUnresolvedTasksWhenClearingOne`

## `scheduler/WaiterTest.java`  (4 tests)

- `should_wait_at_least_duration`
- `should_wait_until_woken`
- `should_wait_for_duration_even_if_prematurely_notified`
- `should_not_wait_if_instructed_to_skip_next`

## `scheduler/compatibility/CompatibilityTest.java`  (16 tests)

- `test_compatibility_fetch_and_lock_on_execute`
- `test_compatibility_lock_and_fetch`
- `test_jdbc_repository_compatibility`
- `test_jdbc_limit_before_after_compatibility`
- `test_jdbc_repository_summary_by_task`
- `test_jdbc_repository_compatibility_with_data`
- `test_jdbc_repository_select_for_update_compatibility`
- `test_jdbc_repository_get_due_correct_order_with_priority`
- `test_jdbc_repository_get_due_correct_order_without_priority`
- `test_jdbc_repository_lock_and_get_due_correct_order_with_priority`
- `test_jdbc_repository_lock_and_get_due_correct_order_without_priority`
- `test_jdbc_repository_support_timestamps_in_far_future`
- `test_has_peristent_time_zone`
- `test_no_drift_when_storing_and_retrieving_instants`
- `test_jdbc_repository_compatibility_set_data`
- `test_compatibility_manual_scheduler`

## `scheduler/compatibility/MssqlCompatibilityTest.java`  (1 tests)

- `test_task_data_byte_array_roundtrip_with_odd_length`

## `scheduler/compatibility/NoAutocommitTest.java`  (1 tests)

- `onstartup_should_always_commit`

## `scheduler/concurrent/MssqlClusterTest.java`  (3 tests)

- `test_concurrency_optimistic_locking`
- `test_concurrency_select_for_update_generic`
- `test_concurrency_recurring`

## `scheduler/concurrent/PostgresClusterTest.java`  (4 tests)

- `test_concurrency_optimistic_locking`
- `test_concurrency_select_for_update`
- `test_concurrency_select_for_update_generic`
- `test_concurrency_recurring`

## `scheduler/event/ExecutionChainTest.java`  (1 tests)

- `happy`

## `scheduler/functional/DeadExecutionTest.java`  (1 tests)

- `test_dead_execution`

## `scheduler/functional/DeleteUnresolvedTest.java`  (1 tests)

- `should_delete_executions_with_old_unresolved_tasknames`

## `scheduler/functional/DetectStaleHeartbeatTest.java`  (1 tests)

- `test_dead_execution`

## `scheduler/functional/DisabledCronTaskTest.java`  (1 tests)

- `should_remove_existing_executions_for_tasks_with_disabled_schedule`

## `scheduler/functional/DynamicRecurringTaskTest.java`  (2 tests)

- `should_schedule_multiple_instances_with_different_schedules`
- `should_support_statechanging_tasks`

## `scheduler/functional/ExecutorPoolTest.java`  (3 tests)

- `test_execute_until_none_left_happy`
- `test_execute_until_none_left_low_polling_limit`
- `test_execute_until_none_left_high_volume`

## `scheduler/functional/ImmediateExecutionTest.java`  (2 tests)

- `test_immediate_execution`
- `test_immediate_execution_by_completion_handler`

## `scheduler/functional/MaxRetriesFailureHandlerTest.java`  (4 tests)

- `maxRetries_thenRemove_should_remove_after_max_failures`
- `maxRetries_then_callback_should_run_when_max_exceeded`
- `deprecated_MaxRetriesFailureHandler_should_still_work`
- `builder_should_throw_when_no_retry_strategy_set`

## `scheduler/functional/PriorityExecutionTest.java`  (2 tests)

- `test_when_priority_is_enabled`
- `test_when_priority_is_disabled`

## `scheduler/functional/RecurringTaskScheduleTest.java`  (5 tests)

- `should_have_starttime_according_to_schedule_by_default`
- `should_have_starttime_now_if_overridden_by_schedule`
- `should_update_preexisting_exeutions_with_new_schedule_if_new_next_execution_time_before_preexisting`
- `should_update_preexisting_exeutions_with_new_deterministic_schedule_if_new_next_execution_time_after_preexisting`
- `should_not_update_data_of_preexisting_exeutions_even_if_rescheduling_because_of_updated_schedule`

## `scheduler/jdbc/ExecutionUpdateTest.java`  (8 tests)

- `should_update_execution_time`
- `should_update_last_success`
- `should_update_last_failed`
- `should_update_picked`
- `should_update_picked_by`
- `should_update_consecutive_failures`
- `should_update_last_heartbeat`
- `should_update_task_data`

## `scheduler/jdbc/JdbcTaskRepositoryExceptionsTest.java`  (5 tests)

- `createIfNotExistsFailsToAddNewTask`
- `getExecutionIsMoreThanOne`
- `removesUnexpectedNumberOfRows`
- `rescheduleUpdatesUnexpectedNumberOfRowsWithoutNewData`
- `rescheduleUpdatesUnexpectedNumberOfRowsWithNewData`

## `scheduler/jdbc/PostgreSqlJdbcCustomizationTest.java`  (1 tests)

- `test`

## `scheduler/jdbc/QueryBuilderTest.java`  (1 tests)

- `test`

## `scheduler/logging/ConfigurableLoggerTest.java`  (3 tests)

- `should_log_using_correct_log_level`
- `should_log_stack_trace_if_configured`
- `should_log_nothing_with_log_level_OFF`

## `scheduler/scrolling/ExecutionTimeAndIdTest.java`  (4 tests)

- `testNullValidation`
- `testEncoding`
- `testInvalidEncoding`
- `testEqualsAndHashCode`

## `scheduler/scrolling/ScheduledExecutionsFilterScrollTest.java`  (2 tests)

- `testScrollingAndLimiting`
- `testValidation`

## `scheduler/scrolling/ScrollingPostgresTest.java`  (1 tests)

- `test_scrolling`

## `scheduler/serializer/gson/GsonSerializerTest.java`  (5 tests)

- `serialize_instant`
- `serialize_cron`
- `serialize_daily`
- `serialize_fixed_delay`
- `serialize_schedule_and_data`

## `scheduler/serializer/jackson/JacksonSerializerTest.java`  (7 tests)

- `serialize_instant`
- `serialize_cron`
- `serialize_daily`
- `serialize_fixed_delay`
- `serialize_plain_schedule_and_data`
- `serialize_schedule_and_data`
- `serialize_cron_schedule_with_unix_cron_style_and_custom_object_mapper`

## `scheduler/serializer/jackson3/Jackson3SerializerTest.java`  (6 tests)

- `serialize_instant`
- `serialize_cron`
- `serialize_daily`
- `serialize_fixed_delay`
- `serialize_plain_schedule_and_data`
- `serialize_schedule_and_data`

## `scheduler/task/AbstractTaskTest.java`  (3 tests)

- `test_default_priority_with_data`
- `test_default_priority_without_data`
- `test_default_priority_with_instance_builder`

## `scheduler/task/CronTest.java`  (11 tests)

- `should_validate_pattern`
- `should_validate_zone`
- `should_generate_next_date_correctly`
- `should_generate_next_date_correctly_with_quartz_cron`
- `should_generate_next_date_correctly_with_unix_cron`
- `should_take_time_zone_into_account`
- `should_always_use_time_zone`
- `should_mark_schedule_as_disabled`
- `should_not_fail_on_cronschedule_without_next_execution_time`
- `validate_cron_schedule_equals`
- `equals_and_hash_code`

## `scheduler/task/DailyTest.java`  (13 tests)

- `should_not_allow_empty_times`
- `should_not_allow_null_zone_id`
- `should_generate_next_date_correctly`
- `should_generate_next_date_correctly_during_standard_time`
- `should_generate_next_date_correctly_during_daylight_saving_time`
- `should_generate_next_date_correctly_on_the_day_before_time_changes_to_daylight_saving`
- `should_generate_next_date_correctly_on_the_day_before_time_changes_to_standard`
- `should_generate_next_date_correctly_on_the_day_when_time_changes_to_daylight_saving`
- `should_generate_next_date_correctly_on_the_day_when_time_changes_to_standard`
- `should_generate_next_date_correctly_on_the_day_when_time_changes_to_standard_exists_twice_should_run_once`
- `should_generate_next_date_correctly_on_the_day_when_time_changes_to_daylight_saving_time_doesnt_exists_should_run_later_then_recover`
- `equals_and_hash_code`
- `to_string`

## `scheduler/task/SchedulesTest.java`  (1 tests)

- `should_validate_pattern`

## `scheduler/task/SerializableSchedulesTest.java`  (1 tests)

- `cron_schedule`

## `scheduler/task/helper/CustomTaskTest.java`  (2 tests)

- `should_have_default_priority`
- `should_override_default_priority`

## `scheduler/task/helper/OneTimeTaskTest.java`  (2 tests)

- `should_have_default_priority`
- `should_override_default_priority`

## `scheduler/task/helper/RecurringTaskTest.java`  (2 tests)

- `should_have_default_priority`
- `should_override_default_priority`

## `scheduler/task/helper/RecurringTaskWithPersistentScheduleTest.java`  (4 tests)

- `should_have_default_priority`
- `should_override_default_priority`
- `should_have_ReviveDeadExecution_as_default_DeadExecutionHandler`
- `should_overwrite_DeadExecutionHandler`

## `scheduler/task/helper/ScheduleRecurringOnStartupTest.java`  (4 tests)

- `checkForNewExecutionTime_no_new_execution_time_if_execution_imminent`
- `checkForNewExecutionTime_updated_if_deterministic_schedule_change`
- `checkForNewExecutionTime_updated_if_non_deterministic_schedule_change_to_sooner`
- `test_differenceGreaterThan`

## `scheduler/task/schedule/CompositeParserTest.java`  (6 tests)

- `returns_schedule_from_the_first_matching_parser`
- `fails_when_no_successful_parsers`
- `fails_when_one_required_delegate_fails`
- `cannot_create_from_null_or_empty_list`
- `examples_should_be_parsable`
- `examples_should_come_from_delegates`

## `scheduler/task/schedule/DailyParserTest.java`  (5 tests)

- `should_return_empty_for_non_matching_string`
- `should_fail_when_unable_to_parse_timezone`
- `should_recognize_correct_schedule_string_without_timezone`
- `should_recognize_correct_schedule_with_timezone_string`
- `examples_should_be_parsable`

## `scheduler/task/schedule/FixedDelayParserTest.java`  (3 tests)

- `should_fail_on_unrecognizable_schedule_string`
- `should_recognize_correct_schedule_string`
- `examples_should_be_parsable`

## `scheduler/task/schedule/FixedDelayTest.java`  (2 tests)

- `equals_and_hash_code`
- `to_string`

## `scheduler/task/schedule/UnrecognizableScheduleTest.java`  (1 tests)

- `contains_correct_message`
