idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "." "../../common"
    REQUIRES esp_driver_uart esp_driver_usb_serial_jtag esp_event esp_netif esp_wifi nvs_flash
)
