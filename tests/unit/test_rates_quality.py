from app.services.rates_quality import reversing_spike_ids


def test_flags_large_reversing_spike_only_after_250_prior_changes():
    values = [100.0]
    for index in range(251):
        values.append(values[-1] + (0.1 if index % 2 else -0.1))
    values.extend([values[-1] + 5, values[-1]])
    points = [(index, value) for index, value in enumerate(values)]
    assert reversing_spike_ids(points) == [252]


def test_does_not_flag_trend_or_unreversed_move():
    values = [100.0]
    for index in range(251):
        values.append(values[-1] + (0.1 if index % 2 else -0.1))
    values.extend([values[-1] + 5, values[-1] + 5])
    assert reversing_spike_ids(list(enumerate(values))) == []
