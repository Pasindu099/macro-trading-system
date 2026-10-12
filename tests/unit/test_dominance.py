from app.services.dominance import shapley_shares


def test_exact_shapley_shares_sum_and_identify_driver():
    factors = [[float(i % 7), float((i * 3) % 11), float((i * 7) % 13),
                float((i * 11) % 17)] for i in range(60)]
    returns = [2 * row[1] + .1 * row[0] for row in factors]
    shares = shapley_shares(returns, factors)
    assert shares is not None
    assert abs(sum(shares.values()) - 1) < 1e-8
    assert shares["rates"] > .9
