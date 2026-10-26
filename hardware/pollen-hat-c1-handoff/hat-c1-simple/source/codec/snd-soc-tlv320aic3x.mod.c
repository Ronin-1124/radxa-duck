#include <linux/module.h>
#define INCLUDE_VERMAGIC
#include <linux/build-salt.h>
#include <linux/elfnote-lto.h>
#include <linux/export-internal.h>
#include <linux/vermagic.h>
#include <linux/compiler.h>

BUILD_SALT;
BUILD_LTO_INFO;

MODULE_INFO(vermagic, VERMAGIC_STRING);
MODULE_INFO(name, KBUILD_MODNAME);

__visible struct module __this_module
__section(".gnu.linkonce.this_module") = {
	.name = KBUILD_MODNAME,
	.arch = MODULE_ARCH_INIT,
};

#ifdef CONFIG_RETPOLINE
MODULE_INFO(retpoline, "Y");
#endif

SYMBOL_CRC(aic3x_regmap, 0xf3db9efe, "_gpl");
SYMBOL_CRC(aic3x_probe, 0x36eadc1e, "");
SYMBOL_CRC(aic3x_remove, 0x267b7d9a, "");

static const struct modversion_info ____versions[]
__used __section("__versions") = {
	{ 0x4a702e14, "snd_soc_dapm_put_enum_double" },
	{ 0x780a84c2, "regcache_cache_only" },
	{ 0x4fd1d55e, "regcache_sync" },
	{ 0x45313481, "devm_kmalloc" },
	{ 0x98280ba8, "of_property_read_variable_u32_array" },
	{ 0x22e8b30, "snd_soc_component_read" },
	{ 0xc5ec965f, "snd_soc_component_test_bits" },
	{ 0x6344332c, "gpiod_set_raw_value" },
	{ 0xd9789195, "snd_soc_put_enum_double" },
	{ 0x298c8a4a, "regcache_mark_dirty" },
	{ 0xfe990052, "gpio_free" },
	{ 0x976f5bb4, "snd_soc_component_write" },
	{ 0x70f949dd, "of_get_named_gpio_flags" },
	{ 0x1cf10d4d, "regulator_get_voltage" },
	{ 0x85d52ce0, "devm_snd_soc_register_component" },
	{ 0x6ac3dd41, "devm_regulator_register_notifier" },
	{ 0x92997ed8, "_printk" },
	{ 0x8da6585d, "__stack_chk_fail" },
	{ 0x9d281499, "regulator_bulk_disable" },
	{ 0xf76c301, "snd_soc_get_enum_double" },
	{ 0xd299e552, "snd_soc_info_volsw" },
	{ 0x12ad97a9, "_dev_err" },
	{ 0xf1616283, "gpiod_direction_output_raw" },
	{ 0x39de17a8, "snd_soc_dapm_put_volsw" },
	{ 0x19522b74, "snd_soc_add_component_controls" },
	{ 0x84a1b765, "_dev_warn" },
	{ 0x1e25d103, "snd_soc_dapm_add_routes" },
	{ 0xcc717a56, "snd_soc_dapm_kcontrol_dapm" },
	{ 0x86a1d1e2, "snd_soc_info_enum_double" },
	{ 0x22457eb4, "regulator_bulk_enable" },
	{ 0x795fe1da, "devm_regulator_bulk_get" },
	{ 0x52a557b7, "snd_soc_get_volsw" },
	{ 0x1bc0347c, "snd_soc_put_volsw" },
	{ 0x93f92d6b, "snd_soc_component_update_bits" },
	{ 0xc7893117, "snd_soc_dapm_new_controls" },
	{ 0xeae3dfd6, "__const_udelay" },
	{ 0x18e5a1c0, "gpio_to_desc" },
	{ 0x47229b5c, "gpio_request" },
	{ 0x9a5e3d70, "snd_soc_dapm_get_volsw" },
	{ 0xd58efc61, "regmap_register_patch" },
	{ 0x770a5fad, "snd_soc_dapm_mixer_update_power" },
	{ 0x90bd2f1, "snd_soc_dapm_get_enum_double" },
	{ 0xe56a9336, "snd_pcm_format_width" },
	{ 0x846d3746, "module_layout" },
};

MODULE_INFO(depends, "");


MODULE_INFO(srcversion, "8252DA591DBC98F291F8245");
