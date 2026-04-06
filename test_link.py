from affiliate_helpers import generate_affiliate_link

if __name__ == "__main__":
    # 1) ضع هنا رابط منتج حقيقي من AliExpress
    # مثال (غيّره إلى رابط حقيقي من حسابك):
    url = "https://ar.aliexpress.com/item/1005009698802944.html"

    # 2) tracking_id يمكن أن يكون 'default' الآن،
    # لاحقاً سنجعله يساوي user_id في تيليغرام لكل مستخدم.
    tracking_id = "default"

    # 3) بلد الشحن (اختيارية الآن، FR كمثال)
    ship_to_country = "FR"

    print("🔄 Generating affiliate link for:", url)
    data = generate_affiliate_link(
        source_url=url,
        tracking_id=tracking_id,
        ship_to_country=ship_to_country
    )

    print("\n🔎 Full JSON response from AliExpress:")
    print(data)
