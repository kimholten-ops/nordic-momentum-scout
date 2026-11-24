from scripts import modelltest


def test_vent_starter_forst_etter_rolig_periode():
    svar = iter([2, 1, 0, 3, 0, 0, 0])
    sov = []
    modelltest.vent_paa_ledig_gpu(rolig_min=3, sov=sov.append, jobber=lambda: next(svar))
    assert len(sov) == 6  # 7 sjekker, sover ikke etter den siste


def test_vent_ledig_med_en_gang():
    sov = []
    modelltest.vent_paa_ledig_gpu(rolig_min=2, sov=sov.append, jobber=lambda: 0)
    assert sov == [60]
