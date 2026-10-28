INSERT INTO airlines (iata_code, name) VALUES
    ('SU', 'Aeroflot'),
    ('S7', 'S7 Airlines'),
    ('DP', 'Pobeda');

INSERT INTO flights (airline_code, number, origin, destination, departure_time, arrival_time,
                     total_seats, available_seats, price, status) VALUES
    ('SU', '1234', 'SVO', 'LED', '2026-12-01 07:00+00', '2026-12-01 08:30+00', 150, 150, 5500.00, 'SCHEDULED'),
    ('S7', '1020', 'SVO', 'LED', '2026-12-01 12:15+00', '2026-12-01 13:45+00', 120, 120, 4900.00, 'SCHEDULED'),
    ('DP', '201',  'VKO', 'LED', '2026-12-01 09:00+00', '2026-12-01 10:25+00', 180, 180, 2990.00, 'SCHEDULED'),
    ('SU', '1234', 'SVO', 'LED', '2026-12-02 07:00+00', '2026-12-02 08:30+00', 150, 150, 6100.00, 'SCHEDULED'),
    ('SU', '1500', 'SVO', 'LED', '2026-12-01 18:00+00', '2026-12-01 19:30+00', 150, 150, 5200.00, 'CANCELLED'),
    ('SU', '1700', 'SVO', 'AER', '2026-12-05 06:00+00', '2026-12-05 10:00+00',   3,   3, 9900.00, 'SCHEDULED'),
    ('S7', '2045', 'LED', 'SVO', '2026-12-03 16:00+00', '2026-12-03 17:30+00', 120, 120, 4700.00, 'SCHEDULED');
