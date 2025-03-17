from flask import Flask, render_template, jsonify, request
import os
from pynspd import Nspd, ThemeId
import json
import traceback
from shapely.geometry import shape, mapping
import logging
import math
from pyproj import Proj, transform

# Настройка логирования
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/map-data')
def get_map_data():
    try:
        # В будущем здесь можно добавить логику загрузки данных
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cadastral', methods=['GET'])
def get_cadastral_data():
    try:
        # Получаем кадастровый номер из запроса
        cadastral_number = request.args.get('cadastral_number')
        if not cadastral_number:
            return jsonify({"error": "Кадастровый номер не указан"}), 400
            
        # Создаем клиент НСПД
        with Nspd() as nspd:
            # Ищем объект по кадастровому номеру
            feature = nspd.find(cadastral_number)
            
            if not feature:
                return jsonify({"error": "Объект не найден"}), 404
                
            # Получаем информацию об объекте
            properties = feature.properties.options.model_dump()
            
            # Получаем геометрию в формате GeoJSON
            geometry = feature.geometry.model_dump()
            
            # Определяем тип объекта
            object_type = properties.get('land_record_type', 'Объект капитального строительства')
            
            # Возвращаем результат
            return jsonify({
                "type": "Feature",
                "properties": properties,
                "geometry": geometry,
                "objectType": object_type
            })
            
    except Exception as e:
        logger.error(f"Ошибка в get_cadastral_data: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cadastral/search_in_contour', methods=['POST'])
def search_in_contour():
    try:
        # Получаем данные из запроса
        data = request.json
        
        if not data or 'geometry' not in data:
            return jsonify({"error": "Не указана геометрия контура"}), 400
            
        # Отладочная информация
        logger.info(f"Получен запрос на поиск в контуре: {data}")
        
        # Получаем геометрию из запроса
        geom_data = data['geometry']
        
        # Отладочная информация о типе геометрии
        logger.info(f"Тип геометрии: {type(geom_data)}")
        logger.info(f"Содержимое геометрии: {geom_data}")
        
        # Преобразуем GeoJSON геометрию в shapely объект для проверки валидности
        try:
            # Преобразуем GeoJSON в shapely геометрию для проверки
            shapely_geom = shape(geom_data)
            logger.info(f"Shapely геометрия создана успешно: {shapely_geom}")
            
            # Проверяем валидность геометрии
            if not shapely_geom.is_valid:
                logger.error("Невалидная геометрия")
                return jsonify({"error": "Предоставлена невалидная геометрия. Пожалуйста, нарисуйте корректный полигон."}), 400
        except Exception as e:
            logger.error(f"Ошибка при преобразовании геометрии: {str(e)}")
            return jsonify({"error": f"Ошибка при обработке геометрии: {str(e)}"}), 400
        
        # Создаем клиент НСПД
        with Nspd() as nspd:
            try:
                # Отладочная информация
                logger.info("Инициализация PyNSPD прошла успешно")
                
                # Получим координаты полигона в исходном формате
                coords = geom_data['coordinates'][0]
                
                # Создаем точки по краям для поиска объектов в bbox вместо polygon
                min_lon = min([p[0] for p in coords])
                min_lat = min([p[1] for p in coords])
                max_lon = max([p[0] for p in coords])
                max_lat = max([p[1] for p in coords])
                
                logger.info(f"Искусственный bbox: [{min_lon},{min_lat}, {max_lon},{max_lat}]")
                
                # Создадим набор точек для поиска
                sw_point = (min_lon, min_lat)  # Юго-западная точка (нижний левый угол)
                ne_point = (max_lon, max_lat)  # Северо-восточная точка (верхний правый угол)
                center_point = ((min_lon + max_lon) / 2, (min_lat + max_lat) / 2)  # Центральная точка
                
                # Вычисляем примерный радиус в километрах (примерно по диагонали bbox)
                dx = (max_lon - min_lon) * 111.32 * math.cos(math.radians((min_lat + max_lat) / 2))
                dy = (max_lat - min_lat) * 110.574
                radius_km = max(math.sqrt(dx**2 + dy**2) / 2, 0.5)  # Минимум 500 метров
                
                logger.info(f"Центр поиска: {center_point}, радиус: {radius_km} км")
                
                # Результирующий список объектов
                result_features = []
                
                # По документации API (https://yazmolod.github.io/pynspd/api/client/), 
                # рекомендуется использовать методы search_zu_in_contour и search_oks_in_contour
                # или search_zu_at_coords и search_oks_at_coords
                
                # Стратегия 1: Попробуем поиск по координатам (это самый надежный метод)
                try:
                    logger.info(f"Пробуем поиск по координатам с радиусом {radius_km} км")
                    
                    # Поиск земельных участков в контуре
                    try:
                        zu_features = list(nspd.search_zu_in_contour(shapely_geom))
                        logger.info(f"Найдено земельных участков в контуре: {len(zu_features)}")
                        result_features.extend(zu_features)
                    except Exception as e_zu:
                        logger.warning(f"Ошибка при поиске ЗУ в контуре: {str(e_zu)}")
                    
                    # Поиск ОКС в контуре
                    try:
                        oks_features = list(nspd.search_oks_in_contour(shapely_geom))
                        logger.info(f"Найдено ОКС в контуре: {len(oks_features)}")
                        result_features.extend(oks_features)
                    except Exception as e_oks:
                        logger.warning(f"Ошибка при поиске ОКС в контуре: {str(e_oks)}")
                    
                except Exception as e:
                    logger.warning(f"Ошибка при поиске по координатам: {str(e)}")
                
                # Стратегия 2: Если объектов не найдено, попробуем через bbox
                if not result_features:
                    try:
                        logger.info("Попытка поиска через box")
                        
                        # Метод поиска земельных участков и ОКС в прямоугольной области
                        try:
                            box_features = list(nspd.search_in_box(sw_point, ne_point))
                            logger.info(f"Найдено объектов через search_in_box: {len(box_features)}")
                            result_features.extend(box_features)
                        except AttributeError:
                            # Если метод search_in_box недоступен, попробуем другие методы
                            if hasattr(nspd, '_iter_search_in_box'):
                                zu_box_features = list(nspd._iter_search_in_box(sw_point, ne_point, layer_def=ThemeId.LAND_PLOT))
                                oks_box_features = list(nspd._iter_search_in_box(sw_point, ne_point, layer_def=ThemeId.OKS))
                                
                                logger.info(f"Найдено ЗУ через _iter_search_in_box: {len(zu_box_features)}")
                                logger.info(f"Найдено ОКС через _iter_search_in_box: {len(oks_box_features)}")
                                
                                result_features.extend(zu_box_features)
                                result_features.extend(oks_box_features)
                        except Exception as e_box:
                            logger.warning(f"Ошибка при поиске через box: {str(e_box)}")
                            
                    except Exception as e_box_outer:
                        logger.warning(f"Ошибка при поиске через box: {str(e_box_outer)}")
                
                # Стратегия 3: Попробуем прямой поиск через контур, если shapely уже создал полигон
                if not result_features and shapely_geom:
                    try:
                        logger.info("Попытка поиска напрямую через контур")
                        
                        from shapely.geometry import Polygon, MultiPolygon
                        
                        # Убедимся, что у нас полигон правильного типа
                        if isinstance(shapely_geom, (Polygon, MultiPolygon)):
                            # Поиск земельных участков в контуре
                            try:
                                zu_contour_features = list(nspd.search_zu_in_contour(shapely_geom))
                                logger.info(f"Найдено ЗУ через search_zu_in_contour: {len(zu_contour_features)}")
                                result_features.extend(zu_contour_features)
                            except Exception as e_zu_contour:
                                logger.warning(f"Ошибка при поиске ЗУ через контур: {str(e_zu_contour)}")
                            
                            # Поиск ОКС в контуре
                            try:
                                oks_contour_features = list(nspd.search_oks_in_contour(shapely_geom))
                                logger.info(f"Найдено ОКС через search_oks_in_contour: {len(oks_contour_features)}")
                                result_features.extend(oks_contour_features)
                            except Exception as e_oks_contour:
                                logger.warning(f"Ошибка при поиске ОКС через контур: {str(e_oks_contour)}")
                    
                    except Exception as e_contour:
                        logger.warning(f"Ошибка при поиске через контур: {str(e_contour)}")
                
                # Стратегия 4: Если предыдущие методы не сработали, попробуем поиск через кадастровые кварталы
                if not result_features:
                    try:
                        logger.info("Попытка поиска через кадастровые кварталы")
                        
                        # Список типичных кадастровых кварталов для Тверской области
                        test_quarters = ["69:18:0070104", "69:40:0100001", "69:10:0000001"]
                        
                        for quarter in test_quarters:
                            try:
                                # Ищем объекты в кадастровом квартале
                                quarter_features = list(nspd.search(quarter))
                                if quarter_features:
                                    logger.info(f"Найдено объектов в квартале {quarter}: {len(quarter_features)}")
                                    
                                    # Фильтруем только те, что потенциально попадают в наш bbox
                                    for feature in quarter_features:
                                        try:
                                            # Получаем координаты объекта и проверяем попадание в bbox
                                            feature_geom = None
                                            if hasattr(feature, 'geometry'):
                                                if hasattr(feature.geometry, 'bounds'):
                                                    feature_bounds = feature.geometry.bounds
                                                    # Если объект хотя бы частично пересекается с нашим bbox
                                                    if (feature_bounds[0] <= max_lon and feature_bounds[2] >= min_lon and 
                                                        feature_bounds[1] <= max_lat and feature_bounds[3] >= min_lat):
                                                        result_features.append(feature)
                                        except Exception as e_feature:
                                            logger.warning(f"Ошибка при обработке объекта из квартала: {str(e_feature)}")
                            except Exception as e_quarter:
                                logger.warning(f"Ошибка при поиске в квартале {quarter}: {str(e_quarter)}")
                    except Exception as e_quarters:
                        logger.warning(f"Ошибка при поиске через кадастровые кварталы: {str(e_quarters)}")
                
                # Если объекты не найдены
                if not result_features:
                    logger.info("Объекты не найдены в указанном контуре")
                    return jsonify({"features": [], "message": "Объекты не найдены в указанном контуре"})
                
                logger.info(f"Всего найдено объектов: {len(result_features)}")
                
                # Удаляем дубликаты по кадастровому номеру
                unique_features = {}
                for feature in result_features:
                    try:
                        # Получаем кадастровый номер объекта
                        cn = None
                        if hasattr(feature, 'properties') and hasattr(feature.properties, 'options'):
                            props = feature.properties.options
                            if hasattr(props, 'cn'):
                                cn = props.cn
                            elif hasattr(props, 'cadastral_number'):
                                cn = props.cadastral_number
                        
                        # Если нашли кадастровый номер, используем его как ключ
                        if cn:
                            unique_features[cn] = feature
                        else:
                            # Если кадастрового номера нет, добавляем объект без проверки на дубликаты
                            # Используем уникальный идентификатор для ключа
                            unique_features[f"object_{len(unique_features)}"] = feature
                    
                    except Exception as e_dup:
                        logger.warning(f"Ошибка при удалении дубликатов: {str(e_dup)}")
                        # В случае ошибки, добавляем объект без проверки на дубликаты
                        unique_features[f"object_{len(unique_features)}"] = feature
                
                if unique_features:
                    logger.info(f"После удаления дубликатов осталось объектов: {len(unique_features)}")
                    result_features = list(unique_features.values())
                
                # Готовим данные в формате GeoJSON
                geoJson_features = []
                for i, feature in enumerate(result_features):
                    try:
                        # Получаем свойства объекта (поддержка различных версий API)
                        properties = {}
                        if hasattr(feature, 'properties'):
                            if hasattr(feature.properties, 'options'):
                                if hasattr(feature.properties.options, 'model_dump'):
                                    properties = feature.properties.options.model_dump()
                                else:
                                    # Пытаемся преобразовать options в словарь
                                    try:
                                        props_dict = {}
                                        for attr_name in dir(feature.properties.options):
                                            if not attr_name.startswith('_'):
                                                try:
                                                    value = getattr(feature.properties.options, attr_name)
                                                    if not callable(value):
                                                        props_dict[attr_name] = value
                                                except:
                                                    pass
                                        properties = props_dict
                                    except:
                                        properties = {"note": "Не удалось извлечь свойства объекта"}
                            elif hasattr(feature.properties, 'model_dump'):
                                properties = feature.properties.model_dump()
                        
                        # Получаем геометрию (поддержка различных версий API)
                        geometry = {}
                        if hasattr(feature, 'geometry'):
                            if hasattr(feature.geometry, 'model_dump'):
                                geometry = feature.geometry.model_dump()
                            elif hasattr(feature.geometry, '__geo_interface__'):
                                geometry = feature.geometry.__geo_interface__
                            else:
                                # Пытаемся преобразовать geometry в словарь GeoJSON
                                try:
                                    if hasattr(feature.geometry, 'type') and hasattr(feature.geometry, 'coordinates'):
                                        geometry = {
                                            'type': feature.geometry.type,
                                            'coordinates': feature.geometry.coordinates
                                        }
                                except:
                                    geometry = {"type": "Point", "coordinates": [0, 0]}
                        
                        # Проверяем валидность геометрии
                        if geometry and 'type' in geometry and 'coordinates' in geometry:
                            geoJson_features.append({
                                "type": "Feature",
                                "properties": properties,
                                "geometry": geometry
                            })
                    except Exception as e:
                        logger.error(f"Ошибка при обработке объекта {i+1}: {str(e)}")
                
                # Возвращаем результат
                logger.info(f"Отправляем результат с {len(geoJson_features)} объектами")
                logger.info(f"Отправляем координаты: {geom_data['coordinates']}")
                logger.info(f"Отправляем данные: {data}")
                return jsonify({
                    "type": "FeatureCollection",
                    "features": geoJson_features
                })
                
            except Exception as e:
                # Отладочная информация
                logger.error(f"Ошибка при поиске в контуре: {str(e)}")
                traceback.print_exc()
                # Проверка на ошибку слишком большого контура
                if "TooBigContour" in str(e):
                    return jsonify({"error": "Выбранная область слишком большая"}), 400
                return jsonify({"error": str(e)}), 500
                
    except Exception as e:
        # Отладочная информация
        logger.error(f"Неожиданная ошибка: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.port = int(os.environ.get('PORT', 5001))
    app.run(debug=True, host='0.0.0.0', port=app.port) 